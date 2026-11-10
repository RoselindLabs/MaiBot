"""Exercise the actual fork functions with synthetic delivery/model dependencies."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import ast
import asyncio
import pytest

from src.chat.replyer import maisaka_generator_base as generator_module
from src.config.config import global_config
from src.maisaka.builtin_tool import reply


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
async def test_receipt_preserves_exact_ids_partial_results_and_never_replays(outcome):
    failure = {"success": None, "failure": TimeoutError("synthetic"), "cancelled": asyncio.CancelledError()}[outcome]
    manager = SimpleNamespace(invoke_hook=AsyncMock(side_effect=failure))
    context = SimpleNamespace(runtime=SimpleNamespace(session_id="synthetic-a"), get_runtime_manager=lambda: manager)
    execution = SimpleNamespace(targets=[True], reply_id="reply-a", call_id="call-a")
    receipts = [
        {"index": 0, "success": True, "message_id": "platform-a", "speech_text": "actually sent", "secret": "omit"},
        {"index": 1, "success": False, "message_id": "", "speech_text": ""},
    ]
    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await reply._notify_reply_delivery(context, execution, receipts)
    else:
        await reply._notify_reply_delivery(context, execution, receipts)
    manager.invoke_hook.assert_awaited_once_with(
        "maisaka.reply.after_send",
        session_id="synthetic-a",
        reply_id="reply-a",
        call_id="call-a",
        send_results=[
            {"index": 0, "success": True, "message_id": "platform-a", "text": "actually sent"},
            {"index": 1, "success": False, "message_id": "", "text": ""},
        ],
    )
    assert receipts[0]["secret"] == "omit"


@pytest.mark.parametrize("targets,receipts", [([], [{"index": 0}]), ([True], [])])
async def test_no_delivery_or_no_extension_produces_no_hook(targets, receipts):
    hook = AsyncMock()
    context = SimpleNamespace(get_runtime_manager=lambda: SimpleNamespace(invoke_hook=hook))
    await reply._notify_reply_delivery(context, SimpleNamespace(targets=targets), receipts)
    hook.assert_not_awaited()


def test_actual_hook_registration_is_readonly_and_bounded():
    source = Path("src/maisaka/chat_loop_service.py").read_text(encoding="utf-8-sig")
    calls = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "HookSpec"
        and any(
            kw.arg == "name" and isinstance(kw.value, ast.Constant) and kw.value.value == "maisaka.reply.after_send"
            for kw in node.keywords
        )
    ]
    assert len(calls) == 1
    spec = eval(
        compile(ast.Expression(calls[0]), "actual-hook-spec", "eval"),
        {
            "HookSpec": lambda **kwargs: kwargs,
            "build_object_schema": lambda fields, **kwargs: {"properties": fields, **kwargs},
        },
    )
    assert spec["default_timeout_ms"] == 5000
    assert spec["allow_abort"] is False
    assert spec["allow_kwargs_mutation"] is False
    assert set(spec["parameters_schema"]["required"]) == {"session_id", "reply_id", "call_id", "send_results"}


@pytest.mark.parametrize("outcome", ["success", "timeout", "cancelled"])
async def test_actual_expression_budget_preserves_success_and_external_cancellation(monkeypatch, outcome):
    generator = object.__new__(generator_module.BaseMaisakaReplyGenerator)
    monkeypatch.setattr(generator, "_resolve_session_id", lambda value: value)
    monkeypatch.setattr(global_config.expression, "use_vector_expression", True)
    expected = SimpleNamespace(
        expression_habits="synthetic", selected_expression_ids=[1], selected_expressions=[{}], hook_abort=None
    )
    cancelled = asyncio.Event()

    async def selection(**kwargs):
        if outcome == "success":
            return expected
        if outcome == "cancelled":
            raise asyncio.CancelledError()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(generator_module.maisaka_expression_selector, "select_for_reply", selection)
    original = asyncio.timeout
    deadline = Mock(side_effect=lambda seconds: original(0.01))
    monkeypatch.setattr(generator_module.asyncio, "timeout", deadline)
    call = generator._build_reply_context([], None, "synthetic", "synthetic-a", AsyncMock())
    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await call
    else:
        result = await call
        assert result.expression_habits == ("synthetic" if outcome == "success" else "")
        assert result.selected_expression_ids == ([1] if outcome == "success" else [])
    deadline.assert_called_once_with(20.0)
    if outcome == "timeout":
        assert cancelled.is_set()
