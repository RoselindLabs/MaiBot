"""Public pinned TTS consumer against actual host receipts and Focus payloads."""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import importlib.util
import asyncio
import os
import subprocess
import sys

import pytest
import neko_pytest_guard  # noqa: F401 -- deny model/QQ network in standalone runs

from src.core.tooling import ToolSpec
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.common.data_models.reply_generation_data_models import LLMCompletionResult, ReplyGenerationResult
from src.config.config import global_config
from src.llm_models.payload_content.context_item import ContextItemBuilder, RoleType
from src.llm_models.payload_content.context_snapshot import serialize_context_item_snapshot
from src.maisaka.builtin_tool import reply
from src.maisaka.builtin_tool.context import BuiltinToolRuntimeContext, PostProcessedReplyMessage
from src.maisaka.context.planner_messages import build_planner_prefix
from src.maisaka.focus.tools import routed_schema
from src.maisaka.reply_supplement import ReplySupplement, ReplySupplementPlan
from src.plugin_runtime.host.message_utils import PluginMessageUtils
from pytests.maisaka.test_planner_text_reply import _message, engine as engine


TTS_PIN = "7be09dec956fbae6a8600eef5df14cf116e18d57"


def load(name, filename):
    root = Path(os.environ["NEKO_PLUGIN_FIXTURES"]) / "neko-tts"
    path = root / filename
    expected = subprocess.check_output(["git", "show", TTS_PIN + ":" + filename], cwd=root)
    assert path.read_bytes().replace(b"\r\n", b"\n") == expected.replace(b"\r\n", b"\n")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("success", [False, True], ids=["unknown", "confirmed"])
async def test_actual_host_receipt_drives_tts_once(success):
    deferred = load("neko_bridge_tts", "deferred_speech.py").DeferredSpeech()
    assert deferred.register("synthetic-a", "reply-a", "call-a", [(0, "planned")])
    voices = []

    async def consume(name, **kwargs):
        assert name == "maisaka.reply.after_send"
        args = [kwargs[key] for key in ("session_id", "reply_id", "call_id", "send_results")]
        voices.extend(deferred.claim(*args))

    context = SimpleNamespace(
        runtime=SimpleNamespace(session_id="synthetic-a"),
        get_runtime_manager=lambda: SimpleNamespace(invoke_hook=consume),
    )
    execution = SimpleNamespace(targets=[True], reply_id="reply-a", call_id="call-a")
    results = [
        {
            "index": 0,
            "success": success,
            "message_id": "platform-a" if success else "",
            "speech_text": "actual sent text",
        }
    ]
    assert deferred.claim("foreign", "reply-a", "call-a", results) == []
    await reply._notify_reply_delivery(context, execution, results)
    await reply._notify_reply_delivery(context, execution, results)
    assert voices == ([(0, "actual sent text")] if success else [])


def test_actual_host_focus_snapshot_is_understood_by_pinned_tts():
    tts = load("neko_bridge_tts_focus", "focus_scope.py")
    scopes = ["qq:group:synthetic-a", "qq:group:synthetic-b"]
    schema = routed_schema(
        ToolSpec(
            name="reply",
            description="synthetic",
            parameters_schema={"type": "object", "properties": {}},
            provider_name="synthetic",
            provider_type="builtin",
        ),
        scopes[0],
    )
    schema["properties"]["session_id"]["enum"] = scopes
    definitions = [{"type": "function", "function": {"name": "reply", "parameters": schema}}]
    items = [
        serialize_context_item_snapshot(
            ContextItemBuilder()
            .set_role(RoleType.User)
            .add_text_content(
                build_planner_prefix(
                    timestamp=datetime(2026, 10, 24),
                    user_name="synthetic",
                    message_id="message",
                    chat_id=scope,
                    include_chat_id=True,
                )
            )
            .add_text_content("synthetic message")
            .build(),
        )
        for scope in scopes
    ]
    assert tts.planner_scopes("", definitions) == tuple(scopes)
    assert [item["parts"][0]["text"] for item in items] == [
        build_planner_prefix(
            timestamp=datetime(2026, 10, 24),
            user_name="synthetic",
            message_id="message",
            chat_id=scope,
            include_chat_id=True,
        )
        for scope in scopes
    ]


@pytest.mark.parametrize("mode", ["voice_only", "text_and_voice", "text_fallback"])
@pytest.mark.parametrize("supplemented", [False, True])
async def test_tts_segments_survive_host_quote_and_visibility_chain(engine, monkeypatch, mode, supplemented):
    audio = load("neko_bridge_audio", "audio.py")
    visibility = load("neko_bridge_visibility", "history_visibility.py").VoiceVisibility()
    clips = [b"synthetic-voice-a", b"synthetic-voice-b"]
    target = engine._runtime._chat_history[0].original_message
    monkeypatch.setattr(global_config.chat.reply_style, "group_quote_frequency", "occasionally")
    generation = ReplyGenerationResult(success=True, completion=LLMCompletionResult(response_text="synthetic reply"))
    generator = SimpleNamespace(generate_reply_with_context=AsyncMock(return_value=(True, generation)))
    monkeypatch.setattr(reply.replyer_manager, "get_replyer", lambda **kwargs: generator)
    monkeypatch.setattr(reply, "_invoke_before_post_process_hook", AsyncMock(return_value=("synthetic reply", {})))
    monkeypatch.setattr(reply, "_resolve_reply_quote", AsyncMock(return_value=(True, 1.0)))
    monkeypatch.setattr(reply, "is_bot_self", lambda *args: False)

    def transform(text, items):
        if mode == "text_fallback":
            return items
        for clip in clips:
            visibility.remember_audio("chat", clip)
        voices = [audio.voice_message(clip) for clip in clips]
        return voices if mode == "voice_only" else items + voices

    extension = SimpleNamespace(
        context=SimpleNamespace(),
        targets=["neko.tts.voice"],
        parameters={"neko.tts.voice": {}},
        reply_id="reply-id",
        call_id="execution",
        extra_prompt="",
        prepare=AsyncMock(),
        transform=AsyncMock(side_effect=transform),
        ensure_active=Mock(),
    )
    monkeypatch.setattr(reply, "ReplyExtensionExecution", lambda **kwargs: extension)
    context = BuiltinToolRuntimeContext(engine, engine._runtime)
    delivered = AsyncMock()
    monkeypatch.setattr(context, "get_runtime_manager", lambda: SimpleNamespace(invoke_hook=delivered))
    monkeypatch.setattr(
        context,
        "post_process_reply_message_items_async",
        AsyncMock(
            return_value=[
                PostProcessedReplyMessage(MessageSequence([TextComponent("first")])),
                PostProcessedReplyMessage(MessageSequence([TextComponent("second")])),
            ]
        ),
    )
    engine._runtime.record_planner_reply = Mock()
    payloads, observed, sent_flags = [], [], []

    async def capture_send(**kwargs):
        # Keep real quote insertion and serialization; replace only external delivery/storage.
        message = _message(f"sent-{len(payloads)}")
        message.raw_message = kwargs["message_sequence"]
        message.processed_plain_text = kwargs["processed_plain_text"]
        await reply.send_service._prepare_message_for_platform_io(
            message,
            typing=False,
            set_reply=kwargs["set_reply"],
            reply_message_id=kwargs["reply_message"].message_id,
            reply_message=kwargs["reply_message"],
        )
        segments = PluginMessageUtils._message_sequence_to_dict(message.raw_message)
        payloads.append(segments)
        sent_flags.append(kwargs["set_reply"])
        observed.append(visibility.observe("chat", {"message_id": message.message_id, "raw_message": segments}, True))
        return message

    send = AsyncMock(side_effect=capture_send)
    monkeypatch.setattr(reply.send_service, "_send_to_target_with_message", send)
    supplement_runner = None
    if supplemented:
        supplement_runner = AsyncMock(
            return_value=ReplySupplement(
                plan=ReplySupplementPlan.model_validate({"msg_id": "original", "parts": [{"type": "reply"}]}),
                target_message=target,
                attachments=asyncio.create_task(asyncio.sleep(0, result=[None])),
                metadata={"synthetic": True},
            )
        )
    result = await reply.execute_reply(
        context,
        {"msg_id": target.message_id, "reply_reference": "synthetic", "plugin_options": {"neko.tts.voice": {}}},
        call_id="execution",
        target_message=target,
        supplement_runner=supplement_runner,
    )
    assert result.success
    types = [[segment["type"] for segment in payload] for payload in payloads]
    text_types = [] if mode == "voice_only" else [["reply", "text"], ["text"]]
    voice_types = [] if mode == "text_fallback" else [["voice"], ["voice"]]
    assert types == text_types + voice_types
    assert observed == [False] * len(text_types) + [True] * len(voice_types)
    assert sent_flags == ([True, False] if text_types else []) + [False] * len(voice_types)
    assert [item["set_quote"] for item in result.metadata["send_results"]] == sent_flags
    assert send.await_count == len(text_types) + len(voice_types)
    delivered.assert_awaited_once()
    assert len(visibility.ids) == len(voice_types)
    assert not visibility.pending
