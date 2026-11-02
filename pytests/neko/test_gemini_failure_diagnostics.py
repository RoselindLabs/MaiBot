"""Real SDK metadata, synthetic responses only; never call a model or replay a request."""

import json
import pytest
from google.genai import types

from src.llm_models.exceptions import EmptyResponseException
from src.llm_models.gemini_failure_diagnostics import gemini_failure_diagnostic


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
