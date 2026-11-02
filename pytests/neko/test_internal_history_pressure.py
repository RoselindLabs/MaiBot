"""Synthetic private-history pressure contracts; no model/QQ or production data."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import asyncio
import json

import pytest

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.common.data_models.mai_message_data_model import MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import (
    ImageComponent,
    MessageSequence,
    ReplyComponent,
    TextComponent,
)
from src.config.model_configs import ModelInfo
from src.llm_models.model_client.base_client import ResponseRequest
from src.llm_models.payload_content.context_item import (
    ContextItemMeta,
    ContextToolCall,
    FunctionCallItem,
    ProviderActivityItem,
    ProviderOpaqueItem,
    ProviderReplayFragment,
    ProviderScope,
    ReasoningItem,
    get_item_text,
)
from src.maisaka.context import compaction
from src.maisaka.context.compaction import ContextSummaryMessage, PlannerRequestSnapshot
from src.maisaka.context.internal_pressure import plan_internal_history_pressure
from src.maisaka.context.messages import (
    InternalContextMessage,
    ModelOutputContextMessage,
    SessionBackedMessage,
    ToolResultMediaMessage,
    ToolResultMessage,
)
from src.maisaka.reasoning_engine import MaisakaReasoningEngine

NOW = datetime(2026, 11, 2, 12)


def chat(index):
    sequence = MessageSequence([TextComponent("synthetic IM"), ReplyComponent("synthetic-quote")])
    original = SessionMessage(str(index), NOW, "synthetic", received_at=NOW)
    original.session_id = "synthetic-private"
    original.message_info = MessageInfo(UserInfo("synthetic", "synthetic"))
    original.raw_message = sequence
    return SessionBackedMessage.from_session_message(
        original,
        raw_message=sequence,
        visible_text="synthetic IM",
        source_kind="user" if index % 2 == 0 else "guided_reply",
    )


def batch(turn, *, parallel=1, media=False, pending=False, provider=None):
    messages = [
        ModelOutputContextMessage(
            ReasoningItem(
                ContextItemMeta.create(logical_turn_id=turn, timestamp=NOW),
                summary_parts=("synthetic reasoning",),
            )
        )
    ]
    if provider:
        meta = ContextItemMeta.create(logical_turn_id=turn, timestamp=NOW)
        if provider == "opaque":
            item = ProviderOpaqueItem(meta, "synthetic")
        elif provider == "activity":
            item = ProviderActivityItem(meta, "synthetic")
        else:
            scope = ProviderScope(1, "synthetic", "synthetic", "synthetic", "synthetic")
            fragment = ProviderReplayFragment.from_payload(scope, {"type": "synthetic"})
            item = ReasoningItem(meta, summary_parts=("synthetic",), replay=fragment)
        messages.append(ModelOutputContextMessage(item))
    for index in range(parallel):
        messages.append(
            ModelOutputContextMessage(
                FunctionCallItem(
                    ContextItemMeta.create(logical_turn_id=turn, timestamp=NOW),
                    ContextToolCall.create(
                        call_id=f"{turn}-{index}", func_name="wait" if pending else "synthetic", args={}
                    ),
                )
            )
        )
    if not pending:
        for index in range(parallel):
            messages.append(
                ToolResultMessage(
                    "synthetic output",
                    NOW,
                    f"{turn}-{index}",
                    turn,
                    tool_name="synthetic",
                )
            )
            if media:
                messages.append(
                    ToolResultMediaMessage(
                        MessageSequence([TextComponent("synthetic media placeholder")]),
                        "synthetic media placeholder",
                        NOW,
                        tool_call_id=f"{turn}-{index}",
                    )
                )
    return messages


def plan(history, *, pending=frozenset(), active=None, trigger=8, target=3):
    return plan_internal_history_pressure(
        history,
        pending_call_ids=frozenset(pending),
        active_turn_id=active,
        trigger=trigger,
        target=target,
    )


def snapshot(history):
    return PlannerRequestSnapshot(
        ResponseRequest(
            model_info=ModelInfo(
                model_type="llm", name="synthetic", model_identifier="synthetic", api_provider="synthetic"
            ),
            context_items=[
                item
                for message in history
                for item in ((message.to_context_item(enable_visual_message=False),))
                if item is not None
            ],
        ),
        tuple(history),
    )


def engine(history, *, pending=frozenset()):
    runtime = SimpleNamespace(
        _chat_history=history,
        _chat_loop_service=SimpleNamespace(planner_request_snapshot=snapshot(history)),
        _get_pending_wait_tool_call_ids=lambda: set(pending),
        chat_stream=SimpleNamespace(is_group_session=False),
        _focus_group=None,
        log_prefix="synthetic-private",
        session_id="synthetic-private",
    )
    return MaisakaReasoningEngine(runtime), runtime


def pressure_history(count=70):
    history = [ContextSummaryMessage("synthetic previous fact", NOW), *[chat(i) for i in range(42)]]
    for index in range(count):
        history.extend(batch(f"turn-{index}"))
    return history


@pytest.fixture
def stub_summary(monkeypatch):
    requests = []

    async def generate(request):
        requests.append(request)
        return LLMResponseResult.from_portable_output(
            response=json.dumps(
                {
                    "topics": ["synthetic topic"],
                    "background": ["synthetic previous fact"],
                    "progress": ["synthetic selected tool conclusions"],
                    "pending": [],
                    "agreements": [],
                }
            )
        )

    monkeypatch.setattr(
        compaction, "LLMServiceClient", lambda **kw: SimpleNamespace(generate_response_from_snapshot=generate)
    )
    monkeypatch.setattr(compaction, "save_compaction_prompt_preview", Mock())
    return requests


@pytest.mark.asyncio
async def test_42_real_250_closed_batches_single_rolling_summary_and_repeat(stub_summary):
    history = pressure_history(250)
    before = tuple(history)
    originals = tuple(message for message in history if isinstance(message, SessionBackedMessage))
    original_ids = tuple(message.context_item_id for message in originals)
    reasoning, runtime = engine(history)
    detail = SimpleNamespace(time_records={})
    await reasoning._compact_private_internal_history(detail)
    assert len(before) == 793 and len(runtime._chat_history) == 139
    assert tuple(history) == before
    assert len(stub_summary) == 1
    assert "synthetic previous fact" in "\n".join(get_item_text(i) for i in stub_summary[0].context_items)
    assert "synthetic output" in "\n".join(get_item_text(i) for i in stub_summary[0].context_items)
    assert "synthetic IM" in "\n".join(get_item_text(i) for i in stub_summary[0].context_items)
    explicit_data = get_item_text(stub_summary[0].context_items[-1])
    assert all(text in explicit_data for text in ("synthetic previous fact", "synthetic output", "synthetic IM"))
    summaries = [m for m in runtime._chat_history if isinstance(m, ContextSummaryMessage)]
    assert len(summaries) == 1 and summaries[0] is not before[0]
    assert "synthetic previous fact" in summaries[0].content
    assert tuple(m for m in runtime._chat_history if isinstance(m, SessionBackedMessage)) == originals
    assert tuple(m.context_item_id for m in originals) == original_ids
    assert all(m.media_sequence.components[1].target_message_id == "synthetic-quote" for m in originals)
    assert runtime._chat_loop_service.planner_request_snapshot is None
    for index in range(70):
        runtime._chat_history.extend(batch(f"next-{index}"))
    runtime._chat_loop_service.planner_request_snapshot = snapshot(runtime._chat_history)
    previous_summary = runtime._chat_history[0]
    reasoning._internal_pressure_attempted = False  # A new natural outer turn.
    await reasoning._compact_private_internal_history(detail)
    assert len(stub_summary) == 2
    assert sum(isinstance(m, ContextSummaryMessage) for m in runtime._chat_history) == 1
    assert runtime._chat_history[0] is not previous_summary
    assert len(runtime._chat_history) == 139


def test_closed_parallel_calls_and_media_are_one_atomic_batch():
    old = batch("old", parallel=2, media=True)
    history = [chat(0), *old, chat(1), *batch("latest")]
    result = plan(history, trigger=9, target=3)
    assert result.status == "ready" and result.removed_batches == 1
    assert tuple(history[i] for i in sorted(result.removed_indices)) == tuple(old)
    assert result.guards.unchanged()


def test_pending_and_active_with_anchors_are_retained():
    waiting = batch("waiting", pending=True)
    history = [chat(0), *batch("old"), chat(1), *waiting, chat(2), *batch("active"), chat(3), *batch("latest")]
    result = plan(history, pending={"waiting-0"}, active="active", trigger=10, target=8)
    assert result.status == "ready"
    assert result.removed_indices == frozenset({1, 2, 3})
    assert all(i not in result.removed_indices for i, message in enumerate(history) if message in waiting)
    assert result.retained_internal_count == 8


@pytest.mark.parametrize("kind", ["span", "media_anchor", "opaque", "activity", "replay", "pending", "unknown_turn"])
def test_protected_or_unknown_dependencies_do_not_force_convergence(kind):
    history = [chat(0)]
    pending = set()
    if kind == "span":
        old = batch("old")
        history.extend([*old[:2], chat(1), *old[2:], chat(2), *batch("latest")])
    elif kind == "media_anchor":
        for index in range(4):
            history.extend(batch(f"media-{index}", media=True))
    elif kind in {"opaque", "activity", "replay"}:
        history.extend([*batch("old", provider=kind), chat(1), *batch("latest")])
    elif kind == "pending":
        history.extend([*batch("old", pending=True), chat(1), *batch("latest")])
        pending.add("old-0")
    else:
        history.append(ModelOutputContextMessage(ReasoningItem(ContextItemMeta.create(), summary_parts=("synthetic",))))
        history.extend(batch("latest"))
    before = tuple(history)
    result = plan(history, pending=pending, trigger=4, target=3)
    assert result.status in {"unreclaimable", "unknown_turn"}
    assert not result.removed_indices and tuple(history) == before


def test_no_pressure_and_insufficient_target_are_explicit():
    history = [chat(0), *batch("last")]
    assert plan(history, trigger=8, target=3).status == "no_pressure"
    assert plan(history, trigger=3, target=1).status == "unreclaimable"


def test_invalid_pairs_and_opaque_component_state_are_never_repaired_by_deletion():
    broken = [chat(0), *batch("old")[:-1], chat(1), *batch("latest")]
    assert plan(broken, trigger=4, target=3).status == "invalid_history"
    from src.common.data_models.message_component_data_model import DictComponent

    opaque = [chat(0), *batch("old"), chat(1), *batch("latest")]
    opaque[0].raw_message.components.append(DictComponent({"synthetic": []}))
    assert plan(opaque, trigger=4, target=3).status == "unsupported_state"


def test_existing_summary_or_missing_anchor_is_not_silently_reanchored():
    history = [*batch("unanchored"), chat(0), *batch("old"), chat(1), *batch("latest")]
    assert plan(history, active="unanchored", trigger=8, target=6).status == "protected_anchor"
    history.insert(0, ContextSummaryMessage("synthetic old anchor", NOW))
    assert plan(history, active="unanchored", trigger=8, target=6).status == "protected_anchor"


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["group", "focus", "no_snapshot"])
async def test_group_focus_and_missing_snapshot_never_request_summary(stub_summary, scope):
    reasoning, runtime = engine(pressure_history())
    if scope == "group":
        runtime.chat_stream.is_group_session = True
    elif scope == "focus":
        runtime._focus_group = object()
    else:
        runtime._chat_loop_service.planner_request_snapshot = None
    original = runtime._chat_history
    await reasoning._compact_private_internal_history(SimpleNamespace(time_records={}))
    assert runtime._chat_history is original and not stub_summary


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "invalid", "empty", "too_long", "tools", "cancel"])
async def test_failed_cancelled_or_invalid_summary_preserves_exact_history(monkeypatch, stub_summary, failure):
    reasoning, runtime = engine(pressure_history())
    original = runtime._chat_history
    old_snapshot = runtime._chat_loop_service.planner_request_snapshot

    async def generate(request):
        if failure == "exception":
            raise RuntimeError("synthetic failure")
        if failure == "cancel":
            raise asyncio.CancelledError()
        if failure == "tools":
            from src.llm_models.payload_content.tool_option import ToolCall

            return LLMResponseResult.from_portable_output(
                response="", tool_calls=[ToolCall("synthetic", "synthetic", {})]
            )
        if failure == "too_long":
            from src.config.config import global_config

            data = {key: [] for key in compaction.STATE_LABELS}
            data["topics"] = ["x" * (global_config.chat.context_compaction_summary_max_chars + 1)]
            return LLMResponseResult.from_portable_output(response=json.dumps(data))
        value = "invalid JSON" if failure == "invalid" else json.dumps({key: [] for key in compaction.STATE_LABELS})
        return LLMResponseResult.from_portable_output(response=value)

    monkeypatch.setattr(
        compaction, "LLMServiceClient", lambda **kw: SimpleNamespace(generate_response_from_snapshot=generate)
    )
    detail = SimpleNamespace(time_records={})
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await reasoning._compact_private_internal_history(detail)
    else:
        await reasoning._compact_private_internal_history(detail)
    assert runtime._chat_history is original
    assert runtime._chat_loop_service.planner_request_snapshot is old_snapshot
    assert original[0].content == "synthetic previous fact"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["append", "replace", "text", "quote", "media", "pending", "active", "snapshot", "focus"]
)
async def test_concurrent_history_component_or_owner_state_changes_are_not_overwritten(
    monkeypatch, stub_summary, change
):
    history = pressure_history()
    # No image decoding: one scalar media component is CAS-protected but the
    # synthetic Planner snapshot still contains only the original text projection.
    image = ImageComponent(binary_hash="synthetic", content="synthetic description")
    reasoning, runtime = engine(history)
    history[1].raw_message.components.append(image)
    original = tuple(history)

    async def summarize(*args, **kwargs):
        if change == "append":
            history.append(chat(99))
        elif change == "replace":
            runtime._chat_history = list(history)
        elif change == "text":
            history[1].visible_text = "synthetic changed"
        elif change == "quote":
            history[1].raw_message.components[1].target_message_id = "synthetic changed quote"
        elif change == "media":
            image.content = "synthetic changed image description"
        elif change == "pending":
            runtime._get_pending_wait_tool_call_ids = lambda: {"turn-69-0"}
        elif change == "active":
            reasoning._active_logical_turn_id = "changed"
        elif change == "snapshot":
            runtime._chat_loop_service.planner_request_snapshot = None
        else:
            runtime._focus_group = object()
        return ContextSummaryMessage("synthetic must not commit", NOW)

    monkeypatch.setattr("src.maisaka.reasoning_engine.build_context_summary", summarize)
    await reasoning._compact_private_internal_history(SimpleNamespace(time_records={}))
    assert not any(
        isinstance(m, ContextSummaryMessage) and m.content == "synthetic must not commit" for m in runtime._chat_history
    )
    assert all(m is before for m, before in zip(runtime._chat_history, original, strict=False))


@pytest.mark.asyncio
async def test_owned_ephemeral_material_not_added_to_summary_and_original_im_quote_survives(stub_summary):
    history = pressure_history()
    control = InternalContextMessage(
        MessageSequence([TextComponent("synthetic unsent control")]),
        "synthetic unsent control",
        NOW,
        source_kind="plugin_proactive:neko.proactive",
    )
    history.insert(1, control)
    reasoning, runtime = engine(history)
    await reasoning._compact_private_internal_history(SimpleNamespace(time_records={}))
    assert len(stub_summary) == 1
    assert control.context_item_id not in {item.meta.item_id for item in stub_summary[0].context_items}
    assert "synthetic unsent control" not in get_item_text(stub_summary[0].context_items[-1])
    assert control in runtime._chat_history
    assert all(
        m.media_sequence.components[1].target_message_id == "synthetic-quote"
        for m in runtime._chat_history
        if isinstance(m, SessionBackedMessage)
    )


@pytest.mark.asyncio
async def test_multiple_preexisting_summaries_are_not_silently_lost(stub_summary):
    history = pressure_history()
    history.insert(1, ContextSummaryMessage("synthetic second old state", NOW))
    reasoning, runtime = engine(history)
    await reasoning._compact_private_internal_history(SimpleNamespace(time_records={}))
    assert runtime._chat_history is history and not stub_summary


@pytest.mark.asyncio
async def test_failure_is_not_retried_for_every_internal_round(monkeypatch, stub_summary):
    reasoning, runtime = engine(pressure_history())
    generate = AsyncMock(side_effect=RuntimeError("synthetic failure"))
    monkeypatch.setattr(
        compaction, "LLMServiceClient", lambda **kw: SimpleNamespace(generate_response_from_snapshot=generate)
    )
    detail = SimpleNamespace(time_records={})
    original = runtime._chat_history
    await reasoning._compact_private_internal_history(detail)
    await reasoning._compact_private_internal_history(detail)
    assert generate.await_count == 1 and runtime._chat_history is original
    reasoning._internal_pressure_attempted = False  # Next natural outer trigger.
    await reasoning._compact_private_internal_history(detail)
    assert generate.await_count == 2 and runtime._chat_history is original


@pytest.mark.asyncio
async def test_end_cycle_only_attempts_pressure_after_successful_planner(monkeypatch):
    runtime = SimpleNamespace(_log_cycle_completed=Mock())
    reasoning = MaisakaReasoningEngine(runtime)
    monkeypatch.setattr(reasoning, "_post_process_chat_history_after_cycle", AsyncMock())
    attempt = AsyncMock()
    monkeypatch.setattr(reasoning, "_compact_private_internal_history", attempt)
    await reasoning._end_cycle(SimpleNamespace(time_records={}))
    attempt.assert_not_awaited()
    await reasoning._end_cycle(SimpleNamespace(time_records={"planner": 1.0}))
    attempt.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_tool_await_finally_end_cycle_starts_no_pressure_work(monkeypatch):
    reasoning, runtime = engine(pressure_history())
    runtime._log_cycle_completed = Mock()
    original = runtime._chat_history
    original_snapshot = runtime._chat_loop_service.planner_request_snapshot
    old_summary = original[0]
    normal_cleanup = AsyncMock()
    monkeypatch.setattr(reasoning, "_post_process_chat_history_after_cycle", normal_cleanup)
    worker = Mock(side_effect=AssertionError("cancelled turn must not start pressure worker"))
    summary = AsyncMock(side_effect=AssertionError("cancelled turn must not request summary"))
    monkeypatch.setattr("src.maisaka.reasoning_engine.plan_internal_history_pressure", worker)
    monkeypatch.setattr("src.maisaka.reasoning_engine.build_context_summary", summary)
    tool_started = asyncio.Event()
    tool_release = asyncio.Event()

    async def cancelled_during_tool():
        detail = SimpleNamespace(time_records={"planner": 1.0})
        try:
            tool_started.set()
            await tool_release.wait()
        except asyncio.CancelledError:
            raise
        finally:
            # Reproduce run_loop's tool cancellation -> finally -> _end_cycle,
            # not a stub raising cancellation from inside summary generation.
            assert asyncio.current_task().cancelling() == 1
            await reasoning._end_cycle(detail)

    task = asyncio.create_task(cancelled_during_tool())
    await tool_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled() and task.cancelling() == 1
    normal_cleanup.assert_awaited_once()  # Existing chat cleanup was not changed.
    runtime._log_cycle_completed.assert_called_once()
    worker.assert_not_called()
    summary.assert_not_awaited()
    assert not reasoning._internal_pressure_attempted
    assert runtime._chat_history is original and original[0] is old_summary
    assert runtime._chat_loop_service.planner_request_snapshot is original_snapshot
