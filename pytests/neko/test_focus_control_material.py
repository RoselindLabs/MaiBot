"""N11 recovery uses N04 material rules without weakening history ownership."""

import copy
from datetime import datetime
from types import SimpleNamespace

import pytest

from pytests.maisaka.test_focus_group_runtime import _chat_history
from pytests.neko.test_focus_recovery import enqueue
from pytests.neko.test_focus_recovery import recovery as recovery, setup_group as setup_group
from src.maisaka.context.compaction import FocusRecoveryError
from src.maisaka.context.messages import ReferenceMessage
from src.maisaka.focus.context import FocusCheckpointStore


def task(scope, stamp):
    return (
        f'<focus_event session_id="{scope}" chat_name="Synthetic">\n'
        f'<plugin_proactive_task id="proactive:neko.proactive:{stamp}" plugin_id="neko.proactive">\n'
        "Synthetic control material\n</plugin_proactive_task>\n</focus_event>"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("restored", [False, True])
async def test_recovery_excludes_owned_controls_before_hooks(recovery, monkeypatch, tmp_path, restored):
    group, _, _, state = recovery
    enqueue(group)
    for scope in ["A", "B"]:
        for i in range(4):
            group._queue_entry(
                scope,
                ReferenceMessage(
                    content=task(scope, 1794300000000 + i),
                    timestamp=datetime.now(),
                    display_prefix="[聊天任务事件]",
                    remaining_uses_value=None,
                ),
            )
    group._take_pending_inputs()
    if restored:
        store = FocusCheckpointStore("synthetic-control", tmp_path)
        store.save(group.entries, {"sequence": group._sequence})
        group.entries = store.load()["entries"]
    inputs = sum(len(records) for entry in group.entries for records in entry.inputs.values())
    calls = []

    class Hooks:
        async def invoke_hook(self, name, **kwargs):
            calls.append(name)
            assert all("plugin_proactive_task" not in str(item) for item in kwargs["items"])
            return SimpleNamespace(kwargs=kwargs)

    monkeypatch.setattr(group._service, "_get_runtime_manager", lambda: Hooks())
    await group._recover_context([])
    assert calls and state.summaries
    assert state.planner == []
    assert group._considered == {}
    assert sum(len(records) for entry in group.entries for records in entry.inputs.values()) == inputs
    assert all("plugin_proactive_task" not in text for batch in state.summaries for text in batch)


@pytest.mark.asyncio
@pytest.mark.parametrize("rewrite", ["none", "drop", "source", "content", "duplicate"])
async def test_real_im_control_quote_remains_owned(recovery, monkeypatch, rewrite):
    group, _, _, state = recovery
    quote = "[聊天任务事件]\n" + task("A", 1794300000000)
    message = _chat_history(quote, "A")
    seen = []

    class Hooks:
        async def invoke_hook(self, name, **kwargs):
            raw = kwargs["items"]
            index = next(
                i for i, item in enumerate(raw) if quote in "".join(p.get("text", "") for p in item.get("parts", []))
            )
            seen.append(copy.deepcopy(raw[index]))
            if rewrite == "drop":
                raw.pop(index)
            elif rewrite == "duplicate":
                raw.append(copy.deepcopy(raw[index]))
            elif rewrite == "source":
                raw[index]["parts"][0]["text"] = raw[index]["parts"][0]["text"].replace(
                    'session_id="A"', 'session_id="foreign"'
                )
            elif rewrite == "content":
                raw[index]["parts"][0]["text"] = "Changed synthetic text"
            return SimpleNamespace(kwargs=kwargs)

    monkeypatch.setattr(group._service, "_get_runtime_manager", lambda: Hooks())
    if rewrite in ["none", "duplicate"]:
        # Duplicate IDs are rejected at protocol decoding; the original survives.
        await group._service.chat_loop_step([message], prepare_focus_compaction=True, tool_definitions=[])
        assert seen
    else:
        with pytest.raises(FocusRecoveryError):
            await group._service.chat_loop_step([message], prepare_focus_compaction=True, tool_definitions=[])
    assert state.planner == state.summaries == []
