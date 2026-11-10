"""Real host classes/builder; no model calls, existing history remains intact."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    ContextItemMeta,
    ContextTextPart,
    ContextToolCall,
    FunctionCallItem,
    FunctionCallOutputItem,
    SystemMessageItem,
    UserMessageItem,
)
from src.maisaka.context import compaction
from src.maisaka.context.messages import InternalContextMessage, ReferenceMessage, SessionBackedMessage


TASK = (
    '<plugin_proactive_task id="proactive:neko.proactive:1792840000000" plugin_id="neko.proactive">\n'
    "unsent control\n</plugin_proactive_task>"
)


def controls():
    now = datetime(2026, 10, 24, 12)
    internal = InternalContextMessage(
        MessageSequence([TextComponent(TASK)]),
        TASK,
        now,
        message_id="proactive:neko.proactive:1792840000000",
        source_kind="plugin_proactive:neko.proactive",
    )
    focus = ReferenceMessage(
        content='<focus_event session_id="synthetic-a" chat_name="synthetic">\n' + TASK + "\n</focus_event>",
        timestamp=now,
        display_prefix="[聊天任务事件]",
        remaining_uses_value=None,
    )
    sequence = MessageSequence([TextComponent(TASK)])
    original = SimpleNamespace(
        message_info=SimpleNamespace(
            user_info=SimpleNamespace(user_cardname="", user_nickname="synthetic", user_id="synthetic")
        ),
        session_id="synthetic-a",
        raw_message=sequence,
    )
    user = SessionBackedMessage(sequence, TASK, now, message_id="actual-user-quote", original_message=original)
    return now, internal, focus, user


@pytest.mark.parametrize("covered", [False, True], ids=["delta", "planner-snapshot"])
def test_real_builder_filters_only_controls_and_preserves_quotes_pairs_summary(monkeypatch, covered):
    now, internal, focus, user = controls()
    monkeypatch.setattr(compaction, "load_prompt", lambda *args, **kwargs: "Synthetic summary instruction")

    def meta(identity):
        return ContextItemMeta.create(item_id=identity, logical_turn_id="synthetic-turn")

    preserved = [
        UserMessageItem(meta(user.context_item_id), (ContextTextPart(TASK),)),
        AssistantMessageItem(meta("real-sent"), (ContextTextPart("Actually sent"),)),
        FunctionCallItem(meta("call"), ContextToolCall.create(call_id="paired", func_name="reply", args={})),
        FunctionCallOutputItem(meta("output"), "paired", "original result", tool_name="reply"),
    ]
    prefix = [SystemMessageItem(meta("persona"), (ContextTextPart("Original persona"),))]
    if covered:
        prefix += [
            SystemMessageItem(meta("neko.life.context.v1:old"), (ContextTextPart("unsent Life background"),)),
            SystemMessageItem(meta("neko.proactive.ephemeral.v1:current"), (ContextTextPart("unsent scene"),)),
            UserMessageItem(meta(internal.context_item_id), (ContextTextPart(TASK),)),
            UserMessageItem(meta(focus.context_item_id), (ContextTextPart(focus.processed_plain_text),)),
        ]
    prefix += preserved
    original_prefix = list(prefix)
    snapshot = SimpleNamespace(
        history=(internal, focus, user) if covered else (),
        request=SimpleNamespace(detached_snapshot=lambda: SimpleNamespace(context_items=list(prefix))),
    )
    summary = compaction.ContextSummaryMessage(content="Existing genuine summary", timestamp=now)
    request = compaction._build_compaction_request(
        [internal, focus, user],
        planner_snapshot=snapshot,
        previous_summary=summary,
        recent_messages=[],
        source_context="synthetic group",
    )
    ids = {item.meta.item_id for item in request.context_items}
    assert internal.context_item_id not in ids and focus.context_item_id not in ids
    assert not any(identity.startswith(("neko.life.context.v1:", "neko.proactive.ephemeral.v1:")) for identity in ids)
    assert all(item in request.context_items for item in preserved)
    assert prefix == original_prefix
    assert "Existing genuine summary" in request.context_items[-1].parts[0].text
    assert compaction.message_text_for_compaction(internal) == ""
    assert compaction.message_text_for_compaction(focus) == ""
    assert "unsent control" in compaction.message_text_for_compaction(user)
    assert summary.content == "Existing genuine summary"


def test_non_owned_reference_and_real_quote_never_become_internal_controls():
    now, _, focus, user = controls()
    assert not compaction._neko_internal_control(user)
    for text, prefix in [
        ("User quoted: " + focus.content, "[聊天任务事件]"),
        (focus.content, "[other plugin]"),
        (focus.content.replace('plugin_id="neko.proactive"', 'plugin_id="other"'), "[聊天任务事件]"),
    ]:
        assert not compaction._neko_internal_control(
            ReferenceMessage(content=text, timestamp=now, display_prefix=prefix)
        )
