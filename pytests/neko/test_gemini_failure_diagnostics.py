"""Real SDK metadata, synthetic responses only; never call a model or replay a request."""

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncio
import json
import pytest
from google.genai import types

from src.llm_models.exceptions import ContentBlockedError, EmptyResponseException, ReqAbortException
from src.llm_models.gemini_failure_diagnostics import gemini_failure_diagnostic


@pytest.mark.parametrize("alternative", ["甲句，乙句", "另一份完整答案"])
def test_normal_gemini_selects_one_candidate_without_mixing_text_reasoning_or_tools(alternative):
    from src.llm_models.model_client.gemini_client import _default_normal_response_parser

    raw = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    parts=[
                        types.Part(text="first reasoning", thought=True),
                        types.Part(text="甲句，"),
                        types.Part(text="乙句"),
                        types.Part(function_call=types.FunctionCall(name="first_tool", args={"n": 1})),
                    ]
                )
            ),
            types.Candidate(
                content=types.Content(
                    parts=[
                        types.Part(text="other reasoning", thought=True),
                        types.Part(text=alternative),
                        types.Part(function_call=types.FunctionCall(name="other_tool", args={"n": 2})),
                    ]
                )
            ),
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=10, candidates_token_count=8),
    )
    before = raw.model_dump()
    parsed, usage = _default_normal_response_parser(raw, logical_turn_id="synthetic")
    assert parsed.content == raw.text == "甲句，乙句"
    assert parsed.reasoning_content == "first reasoning"
    assert [call.func_name for call in parsed.tool_calls] == ["first_tool"]
    assert parsed.raw_data is raw and raw.model_dump() == before
    assert usage is not None


def test_normal_gemini_preserves_deliberate_repetition_inside_first_candidate():
    from src.llm_models.model_client.gemini_client import _default_normal_response_parser

    part = types.Part(text="请重复这句话。请重复这句话。")
    raw = types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(parts=[part]))])
    parsed, _ = _default_normal_response_parser(raw, logical_turn_id="synthetic")
    assert parsed.content == raw.text == "请重复这句话。请重复这句话。"


def test_normal_gemini_does_not_replace_empty_primary_with_an_alternative():
    from src.llm_models.model_client.gemini_client import _default_normal_response_parser

    raw = types.GenerateContentResponse(
        candidates=[
            types.Candidate(content=types.Content(parts=[])),
            types.Candidate(content=types.Content(parts=[types.Part(text="alternative")])),
        ]
    )
    with pytest.raises(EmptyResponseException):
        _default_normal_response_parser(raw, logical_turn_id="synthetic")


def test_reply_shape_distinguishes_candidate_merge_from_repeated_raw_text_without_content():
    from src.llm_models.model_client.gemini_client import _default_normal_response_parser, _normal_reply_shape

    secret = "private original reply"
    raw = types.GenerateContentResponse(
        candidates=[
            types.Candidate(content=types.Content(parts=[types.Part(text=secret)])),
            types.Candidate(content=types.Content(parts=[types.Part(text=secret)])),
        ]
    )
    parsed, _ = _default_normal_response_parser(raw, logical_turn_id="synthetic")
    shape = _normal_reply_shape(raw, parsed)
    assert shape["candidate_count"] == 2 and shape["parsed_matches_first"]
    assert not shape["parsed_exact_double"] and not shape["first_text_exact_double"]
    assert secret not in json.dumps(shape)
    part = types.Part(text=secret * 2)
    raw = types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(parts=[part]))])
    parsed, _ = _default_normal_response_parser(raw, logical_turn_id="synthetic")
    shape = _normal_reply_shape(raw, parsed)
    assert shape["candidate_count"] == 1 and shape["first_text_exact_double"] and shape["parsed_exact_double"]
    assert parsed.content == secret * 2
    assert secret not in json.dumps(shape)
    raw.candidates[0].content.parts = [types.Part(text=secret)] * 20
    parsed, _ = _default_normal_response_parser(raw, logical_turn_id="synthetic")
    shape = _normal_reply_shape(raw, parsed)
    assert len(shape["first_visible_part_lengths"]) == 16 and shape["part_lengths_truncated"]
    raw.candidates[0].content.parts = [types.Part(text=secret)]
    parsed, _ = _default_normal_response_parser(raw, logical_turn_id="synthetic")
    assert _normal_reply_shape(raw, parsed) is None


def request():
    return {
        "request_kind": "response",
        "context_items": [
            {
                "item_type": "UserMessageItem",
                "parts": [
                    {"type": "text", "text": "private input"},
                    {"type": "image", "image_base64": "private image"},
                ],
            },
            {"item_type": "FunctionCallItem", "tool_call": {"args": {"secret": "private argument"}}},
            {"item_type": "FunctionCallOutputItem", "output": "private output"},
        ],
        "model_info": {"force_stream_mode": False},
        "tool_options": [{"name": "synthetic"}],
    }


@pytest.fixture
def snapshot_store(monkeypatch, tmp_path):
    # Match production import order: client package registration precedes snapshots.
    from src.llm_models.model_client.base_client import RequestTraceContext
    from src.llm_models import request_snapshot as snapshots
    from src.maisaka.display.prompt_preview_logger import PromptPreviewLogger

    assert RequestTraceContext is not None
    monkeypatch.setattr(PromptPreviewLogger, "_BASE_DIR", tmp_path)
    monkeypatch.setattr(PromptPreviewLogger, "_image_index_ready", False)
    for name in ("_images_by_preview", "_previews_by_image", "_record_signatures"):
        monkeypatch.setattr(PromptPreviewLogger, name, {})
    monkeypatch.setattr(PromptPreviewLogger, "_index_pending", set())
    monkeypatch.setattr(snapshots, "LLM_REQUEST_LOG_DIR", tmp_path / "errors")
    monkeypatch.setattr(snapshots, "_trim_llm_request_snapshots", lambda: None)


@pytest.mark.parametrize("reason", ["SAFETY", "MAX_TOKENS", "MALFORMED_FUNCTION_CALL", "STOP"])
def test_real_sdk_finish_reason_and_zero_tokens_survive_without_raw_text(reason):
    response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(parts=[]),
                finish_reason=reason,
                finish_message="private provider message with api_key=secret",
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=90,
            candidates_token_count=0,
            thoughts_token_count=0,
            total_token_count=90,
        ),
    )
    diagnostic = gemini_failure_diagnostic(EmptyResponseException(response), request())
    assert diagnostic["response"]["candidates"][0]["finish_reason"] == reason
    assert diagnostic["response"]["usage"]["candidates_token_count"] == 0
    assert diagnostic["response"]["usage"]["cached_content_token_count"] is None
    assert diagnostic["input"] == {"items": 3, "images": 1, "text_chars": 27, "tool_calls": 1, "tool_results": 1}
    serialized = json.dumps(diagnostic)
    assert all(word not in serialized for word in ("private", "secret", "api_key", "image_base64", "finish_message"))


def test_real_sdk_prompt_block_with_no_candidates():
    response = types.GenerateContentResponse(
        prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason="SAFETY"),
    )
    diagnostic = gemini_failure_diagnostic(EmptyResponseException(response), request())
    assert diagnostic["response_metadata_available"] is True
    assert diagnostic["response"]["candidate_count"] == 0
    assert diagnostic["response"]["prompt_block_reason"] == "SAFETY"


def test_timeout_does_not_invent_a_response_or_a_network_cause():
    diagnostic = gemini_failure_diagnostic(TimeoutError(), request())
    assert diagnostic["response_metadata_available"] is False
    assert "response" not in diagnostic
    assert diagnostic["http_status"] is None
    assert diagnostic["input"]["images"] == 1


def test_camel_case_metadata_and_unknown_values_are_allowlisted_and_bounded():
    response = {
        "candidates": [
            {
                "finishReason": "secret-value",
                "finishMessage": "private text",
                "content": {
                    "parts": [{"text": "private text", "thought": True, "functionCall": {"name": "private tool"}}]
                    * 300,
                },
            }
        ]
        * 12,
        "promptFeedback": {"blockReason": "private content"},
        "usageMetadata": {"totalTokenCount": -1, "thoughtsTokenCount": 42, "promptTokenCount": True},
        "headers": {"authorization": "secret"},
    }
    data = gemini_failure_diagnostic(EmptyResponseException(response), request())
    assert data["response"]["candidate_count"] == 12
    assert data["response"]["candidate_count_limited"] is True
    assert len(data["response"]["candidates"]) == 8
    candidate = data["response"]["candidates"][0]
    assert candidate["finish_reason"] == "UNRECOGNIZED"
    assert candidate["part_count_limited"] is True
    assert candidate["function_calls"] == candidate["thought_parts"] == 256
    assert data["response"]["usage"]["thoughts_token_count"] == 42
    assert data["response"]["usage"]["total_token_count"] is None
    assert data["response"]["usage"]["prompt_token_count"] is None
    assert "secret" not in json.dumps(data) and "private" not in json.dumps(data)


def test_existing_snapshot_and_retry_events_carry_diagnostics_without_extra_storage(snapshot_store, tmp_path):
    from src.config.model_configs import APIProvider, ModelInfo
    from src.llm_models.model_client.base_client import RequestTraceContext
    from src.llm_models import request_snapshot as snapshots

    provider = APIProvider(
        name="synthetic", base_url="https://example.invalid/v1beta", api_key="never-print-me", client_type="gemini"
    )
    model = ModelInfo(name="synthetic", model_identifier="synthetic", api_provider="synthetic", model_type="llm")
    trace = RequestTraceContext(task_name="planner", request_type="maisaka.planner")
    error = EmptyResponseException(
        types.GenerateContentResponse(
            candidates=[types.Candidate(finish_reason="MALFORMED_FUNCTION_CALL")],
        )
    )
    path = snapshots.save_failed_request_snapshot(
        api_provider=provider,
        client_type="gemini",
        error=error,
        internal_request=request(),
        model_info=model,
        operation="models.generate_content",
        provider_request={},
        trace_context=trace,
    )
    assert path is not None and path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    diagnostic = data["generation_attempts"][0]["error"]["gemini_diagnostic"]
    assert diagnostic["response"]["candidates"][0]["finish_reason"] == "MALFORMED_FUNCTION_CALL"
    snapshots.attach_request_snapshot(error, path)
    snapshots.update_failed_request_attempt(error, status="retrying", retry_interval=4)
    assert trace.generation_attempts[0].status == "retrying"
    assert trace.generation_attempts[0].error["gemini_diagnostic"] == diagnostic
    assert list(tmp_path.rglob("*.json")) == [path]


def test_diagnostic_failure_cannot_hide_or_replace_the_original_snapshot(snapshot_store, monkeypatch):
    from src.config.model_configs import APIProvider, ModelInfo
    from src.llm_models import request_snapshot as snapshots

    def fail(*args):
        raise ValueError("private provider metadata")

    monkeypatch.setattr(snapshots, "gemini_failure_diagnostic", fail)
    provider = APIProvider(name="synthetic", api_key="not-a-real-key", client_type="gemini")
    error = TimeoutError()
    path = snapshots.save_failed_request_snapshot(
        api_provider=provider,
        client_type="gemini",
        error=error,
        internal_request=request(),
        model_info=ModelInfo(
            name="synthetic", model_identifier="synthetic", api_provider="synthetic", model_type="llm"
        ),
        operation="models.generate_content",
        provider_request={},
    )
    assert path is not None and path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["generation_attempts"][0]["error"]["type"] == "TimeoutError"
    assert "gemini_diagnostic" not in data["generation_attempts"][0]["error"]


def test_http_error_code_available_without_response_body():
    error = EmptyResponseException()
    error.code = 429
    assert gemini_failure_diagnostic(error, request())["http_status"] == 429
    assert gemini_failure_diagnostic(error, request())["response_metadata_available"] is False


def blocked_response(reason="PROHIBITED_CONTENT"):
    return types.GenerateContentResponse(
        prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason=reason),
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=42, total_token_count=42),
    )


def success_response():
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(parts=[types.Part(text="synthetic answer")]))]
    )


@pytest.fixture
def blocked_request_harness(snapshot_store, monkeypatch):
    """Run real client parsing and both orchestration layers; only the transport is fake."""
    from src.config.model_configs import APIProvider, ModelInfo, TaskConfig
    from src.llm_models import utils_model as llm
    from src.llm_models.model_client.gemini_client import GeminiClient
    from src.llm_models.payload_content.context_item import ContextItemBuilder

    task = TaskConfig(model_list=["primary", "backup"], selection_strategy="sequential", hard_timeout=240)
    monkeypatch.setattr(llm.LLMOrchestrator, "_get_task_config_or_raise", lambda self: task)
    orchestrator = llm.LLMOrchestrator("planner", request_type="maisaka.planner")
    provider = APIProvider(
        name="synthetic",
        base_url="https://example.invalid/v1beta",
        api_key="never-print-me",
        client_type="gemini",
        max_retry=4,
        retry_interval=1,
    )
    models = {
        name: ModelInfo(name=name, model_identifier="gemini-2.5-flash", api_provider=provider.name, model_type="llm")
        for name in task.model_list
    }
    monkeypatch.setattr(llm.TempMethodsLLMUtils, "get_model_info_by_name", models.__getitem__)
    monkeypatch.setattr(llm.TempMethodsLLMUtils, "get_provider_by_name", lambda name: provider)
    client = GeminiClient.__new__(GeminiClient)
    client.api_provider = provider
    transport = SimpleNamespace(generate_content=AsyncMock(), generate_content_stream=AsyncMock())
    client.client = SimpleNamespace(aio=SimpleNamespace(models=transport))
    registry = AsyncMock(return_value=client)
    monkeypatch.setattr(llm.client_registry, "get_client_class_instance_async", registry)

    @asynccontextmanager
    async def usage(*args):
        yield

    monkeypatch.setattr(llm, "track_model_task_usage", usage)
    retry = Mock()
    error = Mock()
    monkeypatch.setattr(orchestrator, "_schedule_llm_retry_event", retry)
    monkeypatch.setattr(orchestrator, "_schedule_llm_error_event", error)
    items = [ContextItemBuilder().add_text_part("synthetic input").build()]
    return SimpleNamespace(
        orchestrator=orchestrator,
        transport=transport,
        registry=registry,
        retry=retry,
        error=error,
        items=items,
        request_type=llm.RequestType.RESPONSE,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("diagnostic_fails", [False, True])
async def test_reply_shape_logging_is_single_request_and_cannot_change_response(
    blocked_request_harness, monkeypatch, diagnostic_fails
):
    from src.llm_models.model_client import gemini_client

    h = blocked_request_harness
    h.orchestrator.request_type = "maisaka.replyer"
    secret = "synthetic private reply"
    h.transport.generate_content.return_value = types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(parts=[types.Part(text=secret)])) for _ in range(2)]
    )
    info = Mock()
    monkeypatch.setattr(gemini_client.logger, "info", info)
    if diagnostic_fails:
        monkeypatch.setattr(gemini_client, "_normal_reply_shape", Mock(side_effect=ValueError("private error")))
    result = await h.orchestrator._execute_request(
        h.request_type, context_factory=lambda client: h.items, force_stream_mode=False
    )
    assert result.api_response.content == secret
    h.transport.generate_content.assert_awaited_once()
    h.retry.assert_not_called()
    if diagnostic_fails:
        info.assert_not_called()
    else:
        info.assert_called_once()
        line = info.call_args.args[0]
        assert secret not in line and "never-print-me" not in line
        payload = json.loads(line.split(": ", 1)[1])
        assert payload["candidate_count"] == 2 and payload["parsed_matches_first"] and payload["request_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_prohibited_content_stops_retry_and_fallback_with_original_diagnostic(blocked_request_harness, stream):
    from src.llm_models import request_snapshot as snapshots

    h = blocked_request_harness
    before = deepcopy(h.items)
    raw = blocked_response()
    stream_state = {"closed": False, "continued": False}

    async def chunks():
        try:
            yield raw
            stream_state["continued"] = True
            yield types.GenerateContentResponse()
        finally:
            stream_state["closed"] = True

    h.transport.generate_content.return_value = raw
    h.transport.generate_content_stream.return_value = chunks() if stream else None
    with pytest.raises(ContentBlockedError, match="本轮已停止") as captured:
        await h.orchestrator._execute_request(
            h.request_type,
            context_factory=lambda client: h.items,
            force_stream_mode=stream,
        )
    assert captured.value.ext_info is raw
    assert h.items == before
    assert h.registry.await_count == 1  # No selection of the backup model.
    assert h.transport.generate_content.await_count == (0 if stream else 1)
    assert h.transport.generate_content_stream.await_count == (1 if stream else 0)
    h.retry.assert_not_called()
    h.error.assert_called_once_with(model_name="primary", message=str(captured.value))
    assert h.orchestrator.model_usage == {"primary": (0, 0, 0), "backup": (0, 0, 0)}
    if stream:
        assert stream_state == {"closed": True, "continued": False}
    path = next(snapshots.LLM_REQUEST_LOG_DIR.rglob("*.json"))
    payload = snapshots.read_request_snapshot(path)
    assert payload["metadata"]["status"] == "final_failed"
    assert len(payload["generation_attempts"]) == 1
    attempt = payload["generation_attempts"][0]
    assert attempt["status"] == "final_failed"
    assert attempt["error"]["type"] == "ContentBlockedError"
    diagnostic = attempt["error"]["gemini_diagnostic"]
    assert diagnostic["response"]["prompt_block_reason"] == "PROHIBITED_CONTENT"
    assert diagnostic["response"]["candidate_count"] == 0
    assert diagnostic["response"]["usage"]["total_token_count"] == 42


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [None, "SAFETY", "OTHER", "BLOCK_REASON_UNSPECIFIED"])
async def test_other_empty_responses_keep_retrying_without_rewriting_input(blocked_request_harness, reason):
    h = blocked_request_harness
    raw = types.GenerateContentResponse() if reason is None else blocked_response(reason)
    h.transport.generate_content.side_effect = [raw, success_response()]
    result = await h.orchestrator._execute_request(h.request_type, context_factory=lambda client: h.items)
    assert result.api_response.content == "synthetic answer"
    assert h.transport.generate_content.await_count == 2
    assert h.registry.await_count == 1
    h.retry.assert_called_once()
    h.error.assert_not_called()


@pytest.mark.asyncio
async def test_all_plain_empty_responses_still_switch_to_backup(blocked_request_harness):
    h = blocked_request_harness
    h.transport.generate_content.side_effect = [types.GenerateContentResponse()] * 4 + [success_response()]
    result = await h.orchestrator._execute_request(h.request_type, context_factory=lambda client: h.items)
    assert result.model_info.name == "backup"
    assert h.transport.generate_content.await_count == 5
    assert h.registry.await_count == 2
    assert h.retry.call_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError(), ConnectionError("synthetic network failure")])
async def test_transport_failures_keep_the_existing_retry_policy(blocked_request_harness, failure):
    h = blocked_request_harness
    h.transport.generate_content.side_effect = [failure, success_response()]
    result = await h.orchestrator._execute_request(h.request_type, context_factory=lambda client: h.items)
    assert result.api_response.content == "synthetic answer"
    assert h.transport.generate_content.await_count == 2
    h.retry.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_custom_parsers_cannot_turn_a_block_into_output(blocked_request_harness, stream):
    h = blocked_request_harness
    raw = blocked_response()
    parser = Mock(side_effect=AssertionError("blocked response reached custom parser"))
    received = []

    async def chunks():
        yield raw

    async def handler(source, interrupt):
        async for chunk in source:
            received.append(chunk)
        raise AssertionError("blocked stream completed as success")

    h.transport.generate_content.return_value = raw
    h.transport.generate_content_stream.return_value = chunks() if stream else None
    with pytest.raises(ContentBlockedError):
        await h.orchestrator._execute_request(
            h.request_type,
            context_factory=lambda client: h.items,
            force_stream_mode=stream,
            async_response_parser=parser,
            stream_response_handler=handler,
        )
    parser.assert_not_called()
    assert received == []
    h.retry.assert_not_called()


@pytest.mark.asyncio
async def test_cancellation_is_not_reported_as_content_block(blocked_request_harness):
    h = blocked_request_harness
    h.transport.generate_content.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await h.orchestrator._execute_request(h.request_type, context_factory=lambda client: h.items)
    h.retry.assert_not_called()
    h.error.assert_not_called()
    assert h.orchestrator.model_usage["primary"] == (0, 0, 0)


@pytest.mark.asyncio
async def test_stream_interrupt_precedes_block_and_closes_generator():
    from src.llm_models.model_client.gemini_client import _check_stream_content_blocks

    interrupt = asyncio.Event()
    interrupt.set()
    closed = []

    async def chunks():
        try:
            yield blocked_response()
        finally:
            closed.append(True)

    with pytest.raises(ReqAbortException):
        async for _ in _check_stream_content_blocks(chunks(), interrupt):
            pytest.fail("interrupted stream yielded content")
    assert closed == [True]


@pytest.mark.asyncio
async def test_snapshot_storage_failure_does_not_enable_retries(blocked_request_harness, monkeypatch):
    from src.llm_models import request_snapshot as snapshots

    h = blocked_request_harness
    monkeypatch.setattr(snapshots, "_write_snapshot", Mock(side_effect=OSError("synthetic storage failure")))
    h.transport.generate_content.return_value = blocked_response()
    with pytest.raises(ContentBlockedError):
        await h.orchestrator._execute_request(h.request_type, context_factory=lambda client: h.items)
    assert h.transport.generate_content.await_count == h.registry.await_count == 1
    h.retry.assert_not_called()
    h.error.assert_called_once()
