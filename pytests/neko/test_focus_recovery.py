"""N11 finite recovery through the real worker, request builder and summary builder."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pytests.maisaka.test_focus_group_runtime import setup_group, _chat_history, _response
from src.common.data_models.llm_service_data_models import LLMResponseResult
from src.config.config import global_config
from src.llm_models.exceptions import RespNotOkException
from src.llm_models.payload_content.context_item import get_item_text
from src.maisaka.context import compaction
from src.maisaka.context.compaction import FocusRecoveryError
from src.maisaka.focus.context import FocusCheckpointStore


@pytest.fixture
def recovery(setup_group, monkeypatch):
    group, members, trace = setup_group
    monkeypatch.setattr(global_config.chat, "max_context_size", 6)
    monkeypatch.setattr(global_config.debug, "save_maisaka_reasoning", False)
    group._build_references = AsyncMock(return_value=[])
    service = group._service
    service._custom_chat_system_prompt = "Synthetic recovery only"
    monkeypatch.setattr(service, "_resolve_enable_visual_message", lambda kind: False)
    monkeypatch.setattr(service, "_build_bot_nickname_notice", lambda *a, **kw: "")
    monkeypatch.setattr(service, "_build_current_chat_attention_tail_message", lambda: "")
    monkeypatch.setattr(service, "_build_planner_final_user_reminder", lambda: "")

    class Hooks:
        async def invoke_hook(self, name, **kwargs):
            return SimpleNamespace(kwargs=kwargs)

    monkeypatch.setattr(service, "_get_runtime_manager", lambda: Hooks())
    state = SimpleNamespace(planner=[], summaries=[], failure=None, summary_failure=None, during_summary=None)

    class CapacityClient:
        async def generate_response_with_context(self, *, context_factory, options):
            items = context_factory(None)
            size = sum("recovery-msg-" in get_item_text(item) for item in items)
            state.planner.append(size)
            if state.failure:
                raise state.failure
            if size > 7:
                raise RespNotOkException(400)
            from src.config.model_configs import ModelInfo
            from src.llm_models.model_client.base_client import ResponseRequest
            result = LLMResponseResult.from_portable_output(response="done")
            result.request_snapshot = ResponseRequest(
                model_info=ModelInfo(model_type="llm", name="planner", model_identifier="remote", api_provider="test"),
                context_items=items,
            )
            return result

    class SummaryClient:
        def __init__(self, **kwargs):
            pass

        async def generate_response_with_context(self, *, context_factory, options):
            items = context_factory(None)
            texts = [get_item_text(item) for item in items]
            state.summaries.append(texts)
            # The request contains no original chat Items: only encoded batch data.
            assert all("recovery-msg-" not in text for text in texts[:-1])
            assert texts[-1].count("recovery-msg-") <= 12  # id and body, <=6 messages
            if state.during_summary:
                await state.during_summary()
            if state.summary_failure:
                raise state.summary_failure
            return LLMResponseResult.from_portable_output(response=json.dumps({
                "topics": ["A and B source facts"], "background": [], "progress": [],
                "pending": ["undecided input"], "agreements": [],
            }))

    monkeypatch.setattr(service, "_get_llm_chat_client", lambda kind: CapacityClient())
    monkeypatch.setattr(compaction, "LLMServiceClient", SummaryClient)
    return group, members, trace, state


def enqueue(group, count=8, start=0):
    for i in range(start, start + count):
        sid = "A" if i % 2 == 0 else "B"
        group._queue_entry(sid, _chat_history(f"recovery-msg-{i}", sid))
        group._ready.add(sid)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["first", "after503", "checkpoint", "during_reference"])
async def test_worker_recovers_without_successful_snapshot(recovery, monkeypatch, tmp_path, mode):
    group, members, trace, state = recovery
    if mode == "after503":
        enqueue(group, 1)
        await group._run_turn()
        assert group._service.planner_request_snapshot is not None
        state.failure = RespNotOkException(503)
        enqueue(group, 1, 1)
        await group._run_turn()
        assert group._service.planner_request_snapshot is None
        state.failure = None
        enqueue(group, 8, 2)
    elif mode == "during_reference":
        enqueue(group, 1)
        async def references():
            enqueue(group, 8, 1)
            return []
        group._build_references = references
    else:
        enqueue(group)
    if mode == "checkpoint":
        group._take_ready_inputs()
        store = FocusCheckpointStore("recovery-test", tmp_path)
        store.save(group.entries, {"sequence": group._sequence})
        group.entries = store.load()["entries"]
    completed = asyncio.Event()
    async def checkpoint():
        if group._considered.get("B", 0) >= 8:
            completed.set()
    group.checkpoint = checkpoint
    group._ensure_worker()
    try:
        await asyncio.wait_for(completed.wait(), 3)
        assert state.summaries
        assert state.planner[-1] == 5
        assert trace == []
        assert not group._failed
    finally:
        await group.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["summary", "cancel", "attempts", "entry"])
async def test_failed_recovery_keeps_inputs_and_watermarks(recovery, monkeypatch, failure):
    group, _, trace, state = recovery
    enqueue(group, 60 if failure == "attempts" else 8)
    group._take_ready_inputs()
    if failure == "entry":
        group.entries[0].messages.extend(_chat_history(f"huge-{i}") for i in range(8))
    before = list(group.entries)
    inputs = [dict(entry.inputs) for entry in group.entries]
    if failure == "summary":
        state.summary_failure = RespNotOkException(503)
    if failure == "cancel":
        state.summary_failure = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else FocusRecoveryError):
        await group._recover_context([])
    assert group.entries == before
    assert [entry.inputs for entry in group.entries] == inputs
    assert group._considered == {}
    assert trace == []
    if failure in {"summary", "cancel"}:
        assert len(state.summaries) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["generation", "member", "invalid", "clear", "config", "mutate"])
async def test_late_summary_cannot_commit(recovery, monkeypatch, change):
    group, _, _, state = recovery
    enqueue(group)
    group._take_ready_inputs()
    before = list(group.entries)
    async def change_state():
        if change == "generation":
            group._generation += 1
        elif change == "member":
            group.members.pop("B")
        elif change == "invalid":
            monkeypatch.setattr(group, "is_member_valid", lambda member: False)
        elif change == "clear":
            group.entries.clear()
        elif change == "config":
            monkeypatch.setattr(global_config.chat, "max_context_size", 7)
        else:
            group.entries[0].inputs.clear()
    state.during_summary = change_state
    with pytest.raises(FocusRecoveryError):
        await group._recover_context([])
    assert group.entries == ([] if change == "clear" else before)
    assert group._considered == {}
    assert len(state.summaries) == 1


@pytest.mark.asyncio
async def test_multibatch_recovery_merges_summary_preserves_progress(recovery):
    group, _, trace, state = recovery
    enqueue(group, 20)
    group._take_ready_inputs()
    inputs = sum(len(records) for entry in group.entries for records in entry.inputs.values())
    await group._recover_context([])
    assert len(state.summaries) == 3
    assert "undecided input" in state.summaries[1][-1]
    assert sum(len(records) for entry in group.entries for records in entry.inputs.values()) == inputs
    assert group._considered == {}
    assert trace == []


@pytest.mark.asyncio
async def test_later_batch_failure_does_not_commit_partial_summary(recovery):
    group, _, _, state = recovery
    enqueue(group, 20)
    group._take_ready_inputs()
    before = list(group.entries)
    async def fail_second_batch():
        if len(state.summaries) == 2:
            state.summary_failure = RespNotOkException(400)
    state.during_summary = fail_second_batch
    with pytest.raises(FocusRecoveryError):
        await group._recover_context([])
    assert len(state.summaries) == 2
    assert group.entries == before
    assert all(a is b for a, b in zip(group.entries, before, strict=True))
    assert group._considered == {}


@pytest.mark.asyncio
async def test_arrival_during_summary_stays_unconsumed(recovery):
    group, _, _, state = recovery
    enqueue(group)
    group._take_ready_inputs()
    async def arrival():
        enqueue(group, 1, 8)
    state.during_summary = arrival
    await group._run_turn()
    assert group._considered == {"A": 7, "B": 8}
    assert group._pending["A"][0].inputs == {"A": [(9, "recovery-msg-8")]}


@pytest.mark.asyncio
@pytest.mark.parametrize("rewrite", [False, True])
async def test_hook_material_is_retained_or_ownership_rejected(recovery, monkeypatch, rewrite):
    from src.llm_models.payload_content.context_item import ContextItemBuilder, RoleType
    from src.plugin_runtime.hook_payloads import serialize_prompt_items

    group, _, _, state = recovery
    enqueue(group)
    group._take_ready_inputs()
    before = list(group.entries)
    class Hooks:
        async def invoke_hook(self, name, **kwargs):
            items = kwargs["items"]
            if rewrite:
                items.pop(1)
            items.extend(serialize_prompt_items([
                ContextItemBuilder().set_role(RoleType.User).add_text_content("hook-owned-reference").build()
            ]))
            return SimpleNamespace(kwargs=kwargs)
    monkeypatch.setattr(group._service, "_get_runtime_manager", lambda: Hooks())
    if rewrite:
        with pytest.raises(FocusRecoveryError):
            await group._recover_context([])
        assert group.entries == before
        assert state.summaries == []
    else:
        await group._recover_context([])
        assert any("hook-owned-reference" in text for text in state.summaries[0][:-1])


@pytest.mark.asyncio
async def test_restored_unknown_batch_is_data_never_replayed(recovery):
    from src.llm_models.payload_content.tool_option import ToolCall
    from src.llm_models.payload_content.context_item import FunctionCallItem, FunctionCallOutputItem
    from src.maisaka.focus.context import FocusContextEntry
    from src.maisaka.focus.group_runtime import FocusGroupRuntime
    from src.maisaka.context.messages import ToolResultMessage

    group, _, trace, state = recovery
    response = _response(calls=[ToolCall("pending", "echo", {"session_id": "A", "text": "x"})])
    group.entries = [
        FocusContextEntry([_chat_history("tool-anchor")], {"A"}),
        FocusContextEntry(list(response.raw_messages), {"A"}),
    ]
    await FocusGroupRuntime.checkpoint(group)
    group.entries = group._store.load()["entries"]
    unknown = next(m for m in group.history if isinstance(m, ToolResultMessage))
    assert unknown.metadata["execution_state"] == "unknown"
    enqueue(group, 12)
    group._take_ready_inputs()
    await group._recover_context([])
    assert unknown.content in state.summaries[0][-1]
    assert '"call_id": "pending"' in state.summaries[0][-1]
    assert trace == []
    assert group._considered == {}
    assert not any(isinstance(m.to_context_item(), (FunctionCallItem, FunctionCallOutputItem)) for m in group.history)
