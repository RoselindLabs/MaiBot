"""Bounded, allowlisted Gemini failure metadata; never serialize response bodies."""

from collections.abc import Mapping
from enum import Enum
from typing import Any


FINISH_REASONS = frozenset(
    "FINISH_REASON_UNSPECIFIED STOP MAX_TOKENS SAFETY RECITATION LANGUAGE OTHER "
    "BLOCKLIST PROHIBITED_CONTENT SPII MALFORMED_FUNCTION_CALL IMAGE_SAFETY "
    "IMAGE_PROHIBITED_CONTENT IMAGE_OTHER NO_IMAGE IMAGE_RECITATION UNEXPECTED_TOOL_CALL "
    "TOO_MANY_TOOL_CALLS MISSING_THOUGHT_SIGNATURE MALFORMED_RESPONSE ESCALATION "
    "PUP_LIMITED_DISABLED CONTINUATION".split()
)
BLOCK_REASONS = frozenset("BLOCK_REASON_UNSPECIFIED SAFETY OTHER BLOCKLIST PROHIBITED_CONTENT IMAGE_SAFETY".split())


def _field(value: Any, snake: str, camel: str = "") -> Any:
    if isinstance(value, Mapping):
        return value.get(snake, value.get(camel))
    return getattr(value, snake, None)


def _reason(value: Any, allowed: frozenset[str]) -> str | None:
    if isinstance(value, Enum):
        value = value.value
    if value is None:
        return None
    return value if isinstance(value, str) and value in allowed else "UNRECOGNIZED"


def _number(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 10**12 else None


def gemini_failure_diagnostic(error: Exception, request: Mapping[str, Any]) -> dict[str, Any]:
    """Extract counts/enums only, including when no response arrived before timeout.

    No model_dump(), headers, URLs, signatures, generated text, finish messages or
    provider-supplied free text enter this new diagnostic. Existing snapshot/retry
    behavior is unchanged. Missing response metadata does not prove zero bytes arrived.
    """
    items = request.get("context_items") or []
    counts = {"items": len(items), "images": 0, "text_chars": 0, "tool_calls": 0, "tool_results": 0}
    for item in items[:4096]:
        if not isinstance(item, Mapping):
            continue
        counts["tool_calls"] += item.get("item_type") == "FunctionCallItem"
        counts["tool_results"] += item.get("item_type") == "FunctionCallOutputItem"
        if item.get("item_type") == "FunctionCallOutputItem" and isinstance(item.get("output"), str):
            counts["text_chars"] += len(item["output"])
        for part in (item.get("parts") or [])[:256]:
            if not isinstance(part, Mapping):
                continue
            counts["images"] += part.get("type") == "image"
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                counts["text_chars"] += len(part["text"])
    response = getattr(error, "ext_info", None)
    result: dict[str, Any] = {
        "version": 1,
        "input": counts,
        "input_count_limited": len(items) > 4096
        or any(isinstance(item, Mapping) and len(item.get("parts") or []) > 256 for item in items[:4096]),
        "tools": len(request.get("tool_options") or []),
        "stream": bool((request.get("model_info") or {}).get("force_stream_mode", False)),
        "http_status": _number(getattr(error, "status_code", None)) or _number(getattr(error, "code", None)),
        "response_metadata_available": response is not None,
    }
    if response is None:
        return result
    candidates = _field(response, "candidates") or []
    feedback = _field(response, "prompt_feedback", "promptFeedback")
    usage = _field(response, "usage_metadata", "usageMetadata")
    result["response"] = {
        "candidate_count": len(candidates),
        "candidate_count_limited": len(candidates) > 8,
        "prompt_block_reason": _reason(_field(feedback, "block_reason", "blockReason"), BLOCK_REASONS),
        "usage": {
            name: _number(_field(usage, name, camel))
            for name, camel in (
                ("prompt_token_count", "promptTokenCount"),
                ("candidates_token_count", "candidatesTokenCount"),
                ("thoughts_token_count", "thoughtsTokenCount"),
                ("total_token_count", "totalTokenCount"),
                ("cached_content_token_count", "cachedContentTokenCount"),
            )
        },
        "candidates": [],
    }
    for candidate in candidates[:8]:
        parts = _field(_field(candidate, "content"), "parts") or []
        scanned = parts[:256]
        result["response"]["candidates"].append(
            {
                "finish_reason": _reason(_field(candidate, "finish_reason", "finishReason"), FINISH_REASONS),
                "parts": len(parts),
                "part_count_limited": len(parts) > 256,
                "text_parts": sum(isinstance(_field(p, "text"), str) and bool(_field(p, "text")) for p in scanned),
                "thought_parts": sum(_field(p, "thought") is True for p in scanned),
                "function_calls": sum(_field(p, "function_call", "functionCall") is not None for p in scanned),
            }
        )
    return result
