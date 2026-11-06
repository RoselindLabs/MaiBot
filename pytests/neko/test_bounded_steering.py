"""Event-driven ordinary run_loop contracts; model/IO boundaries are synthetic."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import asyncio

import pytest

from src.config.config import global_config
from src.maisaka import reasoning_engine as engine_module
from src.maisaka.reasoning_engine import MaisakaReasoningEngine, TurnStartContext
from src.maisaka.runtime import MaisakaHeartFlowChatting, PlannerInterruptController
from pytests.maisaka.test_planner_text_reply import _response


class LoopHarness:
    def __init__(self, monkeypatch, *, steering=True, preparation_race=False):
        monkeypatch.setattr(global_config.chat.reply_timing, "planner_message_steering", steering)
        self.pending = []
        self.ingested = []
        self.started = asyncio.Queue()
        self.decisions = asyncio.Queue()
        self.finalized = []
        self.children = []
        self.baseline_tasks = asyncio.all_tasks()
        self.cancelled = 0
        self.preparation_race = preparation_race
        self.preparations = 0
        self.failure = None
        self.runtime = SimpleNamespace(
            _running=True, _internal_turn_queue=asyncio.Queue(),
            _agent_state="running", _STATE_RUNNING="running", _STATE_WAIT="wait",
            _planner_interrupt=PlannerInterruptController(), _chat_history=[],
            message_cache=[],
            _max_context_size=100, _max_internal_rounds=3, _cycle_counter=1,
            _consecutive_wait_count=0, session_id="synthetic", log_prefix="[test]",
            _has_pending_messages=lambda: bool(self.pending),
            _wait_for_message_quiet_period=AsyncMock(),
            _mark_message_turn_unscheduled=Mock(), _collect_pending_messages=self.collect,
            _consume_forced_turn_reason=lambda: None, _start_planner_continuation=Mock(),
            _update_stage_status=Mock(), _enter_stop_state=Mock(), _log_internal_loop_cancelled=Mock(),
        )
        self.runtime._chat_loop_service = SimpleNamespace(
            set_interrupt_flag=Mock(), chat_loop_step=self.model,
        )
        self.engine = MaisakaReasoningEngine(self.runtime)
        self.engine._prepare_turn_start_context = AsyncMock(return_value=TurnStartContext(
            [SimpleNamespace(message_id="initial")], SimpleNamespace(message_id="initial"),
            False, False, False,
        ))
        self.engine._start_cycle_round = AsyncMock(return_value=(SimpleNamespace(time_records={}), "round"))
        self.engine._refresh_visual_placeholders_for_cycle = AsyncMock()
        self.engine._post_process_chat_history_after_cycle = AsyncMock()
        self.engine._build_action_tool_definitions = self.prepare
        self.engine._refresh_jargon_reference_message = Mock(return_value=None)
        self.engine._build_planner_injected_user_messages = AsyncMock(return_value=[])
        self.engine._emit_flow_step = AsyncMock()
        self.engine._ingest_messages = self.ingest
        self.engine._finalize_cycle = self.finalize
        self.engine._handle_planner_response_actions = self.action
        monkeypatch.setattr(engine_module, "resolve_enable_visual_planner", lambda: True)

    def collect(self):
        messages, self.pending = self.pending, []
        return messages

    async def ingest(self, messages):
        self.ingested.extend(messages)

    def message(self, name):
        message = SimpleNamespace(message_id=name)
        self.pending.append(message)
        MaisakaHeartFlowChatting._request_planner_interrupt_for_message(self.runtime, message)

    async def prepare(self):
        self.preparations += 1
        if self.preparation_race:
            self.message(f"prepare-{self.preparations}")
        return [], ""

    async def model(self, *args, **kwargs):
        self.children.append(asyncio.current_task())
        release = asyncio.Event()
        self.started.put_nowait(release)
        try:
            await release.wait()
            if self.failure:
                raise self.failure
            return _response()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise

    async def action(self, **kwargs):
        # Real request binding must already be gone before any tool side effects.
        assert self.runtime._planner_interrupt.request() == "idle"
        self.decisions.put_nowait(kwargs["state"].response)
        return False

    async def finalize(self, **kwargs):
        self.finalized.append((kwargs["round_index"], kwargs["state"].planner_interrupted))
        return kwargs["state"].cycle_end

    def start(self):
        self.runtime._internal_turn_queue.put_nowait("message")
        return asyncio.create_task(self.engine.run_loop())

    async def next_request(self):
        return await asyncio.wait_for(self.started.get(), 2)

    async def close(self, task):
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert self.engine._planner_message_interrupts == 0
        assert self.runtime._planner_interrupt.request() == "idle"
        assert self.runtime._chat_loop_service.set_interrupt_flag.call_args.args == (None,)
        assert all(child.done() for child in self.children)
        assert not (asyncio.all_tasks() - self.baseline_tasks)


@pytest.mark.asyncio
async def test_continuous_messages_complete_decisions_and_reset_budget(monkeypatch):
    h = LoopHarness(monkeypatch)
    task = h.start()
    try:
        for decision in range(2):
            for retry in range(2):
                await h.next_request()
                h.message(f"interrupt-{decision}-{retry}")
            release = await h.next_request()
            for index in range(20):
                h.message(f"queued-{decision}-{index}")
            assert h.runtime._planner_interrupt.request() == "idle"
            release.set()
            await asyncio.wait_for(h.decisions.get(), 2)
        await h.next_request()  # boundary consumed the second protected batch
        assert h.cancelled == 4
        assert len(h.ingested) == 44
        assert not h.pending
        assert h.finalized[:6] == [(0, True), (0, True), (0, False), (1, True), (1, True), (1, False)]
        assert h.runtime._wait_for_message_quiet_period.await_count == 6
    finally:
        await h.close(task)


@pytest.mark.asyncio
async def test_preparation_race_consumes_budget_and_retains_protected_input(monkeypatch):
    h = LoopHarness(monkeypatch, preparation_race=True)
    task = h.start()
    try:
        # First two tasks are cancelled before their coroutine starts, from pending-input check.
        release = await h.next_request()
        assert h.preparations == 3
        assert len(h.ingested) == 2
        assert [m.message_id for m in h.pending] == ["prepare-3"]
        release.set()
        await asyncio.wait_for(h.decisions.get(), 2)
    finally:
        await h.close(task)


@pytest.mark.asyncio
@pytest.mark.parametrize("protected", [False, True])
async def test_external_cancel_cleans_request_and_next_turn(monkeypatch, protected):
    h = LoopHarness(monkeypatch)
    task = h.start()
    if protected:
        for index in range(2):
            await h.next_request()
            h.message(f"interrupt-{index}")
    await h.next_request()
    h.message("retained")
    await h.close(task)
    # A fresh run after cancellation can be interrupted again.
    h.pending.clear()
    task = h.start()
    try:
        await h.next_request()
        h.message("new-turn")
        await h.next_request()
        assert h.engine._planner_message_interrupts == 1
    finally:
        await h.close(task)


@pytest.mark.asyncio
async def test_disabled_steering_keeps_request_and_messages(monkeypatch):
    h = LoopHarness(monkeypatch, steering=False)
    task = h.start()
    try:
        release = await h.next_request()
        for index in range(20):
            h.message(str(index))
        release.set()
        await asyncio.wait_for(h.decisions.get(), 2)
        await h.next_request()
        assert h.cancelled == 0
        assert len(h.ingested) == 20
        assert h.engine._planner_message_interrupts == 0
    finally:
        await h.close(task)


@pytest.mark.asyncio
async def test_protected_request_failure_ends_turn_without_retry(monkeypatch):
    h = LoopHarness(monkeypatch)
    task = h.start()
    for index in range(2):
        await h.next_request()
        h.message(str(index))
    release = await h.next_request()
    h.message("unconsumed")
    h.failure = ValueError("synthetic failure")
    release.set()
    with pytest.raises(ValueError, match="synthetic failure"):
        await task
    assert h.engine._planner_message_interrupts == 0
    assert [m.message_id for m in h.pending] == ["unconsumed"]
    assert len(h.children) == 3
    assert all(child.done() for child in h.children)
    assert not (asyncio.all_tasks() - h.baseline_tasks)
    assert h.runtime._planner_interrupt.request() == "idle"
