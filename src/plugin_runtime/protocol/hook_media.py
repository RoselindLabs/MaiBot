"""Negotiated, per-frame duplicate Hook media transport. Public kwargs contain no refs.

Only repeated canonical Base64 at known ContextItem image paths is projected.
Unique images and other spellings remain literal, preserving snapshot/replay
equality without decoding/hashing unique media. There is no cross-frame cache,
file access or lookup API. Wire AND restored legacy Envelope stay within the
existing frame budget; all temporary maps are bounded by that same payload.
"""

from dataclasses import dataclass
from typing import Any, Mapping

import base64
import binascii
import hashlib

from .codec import Codec
from .envelope import Envelope
from ..transport.base import MAX_FRAME_SIZE

CAPABILITY = "hook-media-binary-v1"
HOOKS = frozenset({"maisaka.planner.before_request", "maisaka.replyer.before_model_request"})
_TAG = "hook_media_wire"
_ITEMS = frozenset({"SystemMessageItem", "UserMessageItem", "AssistantMessageItem"})


@dataclass(frozen=True)
class HookContext:
    request_id: int
    plugin_id: str
    hook: str

    def matches(self, envelope: Envelope) -> bool:
        return (envelope.request_id == self.request_id and envelope.plugin_id == self.plugin_id
                and envelope.method == "plugin.invoke_hook")


class MediaWireError(ValueError):
    """Contains only routing headers, never media/kwargs in diagnostic errors."""

    def __init__(self, message: str, header: Envelope | None = None):
        super().__init__(message)
        self.header = header


def request_context(envelope: Envelope) -> HookContext | None:
    if not envelope.is_request() or envelope.method != "plugin.invoke_hook":
        return None
    args = envelope.payload.get("args")
    hook = args.get("hook_name") if isinstance(args, dict) else None
    if not isinstance(hook, str) or hook not in HOOKS:
        return None
    return HookContext(envelope.request_id, envelope.plugin_id, hook)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MediaWireError(message)


def _bounded(size: int) -> None:
    _require(size <= MAX_FRAME_SIZE, "Hook media exceeds 32 MiB legacy/wire budget")


def _items(raw: dict[str, Any], context: HookContext) -> list[Any] | None:
    payload = raw["payload"]
    kwargs = payload.get("args") if raw["message_type"] == "request" else payload.get("modified_kwargs")
    if not isinstance(kwargs, dict) or kwargs.get("item_schema_version") != 1:
        return None
    if raw["message_type"] == "request" and kwargs.get("hook_name") != context.hook:
        return None
    items = kwargs.get("items")
    return items if isinstance(items, list) else None


def _slots(items: list[Any] | None):
    if items is None:
        return
    for i, item in enumerate(items):
        if not isinstance(item, dict) or item.get("item_type") not in _ITEMS:
            continue
        parts = item.get("parts")
        if not isinstance(parts, list):
            continue
        for j, part in enumerate(parts):
            if isinstance(part, dict) and part.get("type") == "image":
                yield i, j, part


def _canonical(text: Any) -> bytes | None:
    if not isinstance(text, str) or not text or not text.isascii():
        return None
    try:
        raw = base64.b64decode(text, validate=True)
    except (ValueError, binascii.Error):
        return None
    return raw if base64.b64encode(raw).decode("ascii") == text else None


def encode_frame(codec: Codec, envelope: Envelope, enabled: bool,
                 context: HookContext | None = None) -> bytes:
    """CPU work: callers run this off their event loop, under send backpressure."""
    raw = envelope.model_dump()
    ordinary = codec.encode(raw)
    _bounded(len(ordinary))
    context = context or request_context(envelope)
    if not enabled or context is None or not context.matches(envelope) or envelope.error:
        return ordinary
    items = _items(raw, context)
    # Group only known image slots in this already-bounded Envelope. Keys refer
    # to existing strings; do not decode/re-encode or SHA-hash unique images.
    occurrences: dict[str, list[tuple[int, int, dict[str, Any]]]] = {}
    for i, j, part in _slots(items):
        text = part.get("image_base64")
        if isinstance(text, str):
            occurrences.setdefault(text, []).append((i, j, part))
    repeated = [(text, positions) for text, positions in occurrences.items() if len(positions) > 1]
    if not repeated:
        return ordinary
    refs: list[list[int]] = []
    media: list[dict[str, Any]] = []
    by_digest: dict[str, int] = {}
    for text, positions in repeated:
        # Each repeated string is validated/decoded/digested only once per frame.
        binary = _canonical(text)
        if binary is None:
            continue  # Literal stays EXACTLY as provided, not normalized/rejected.
        digest = hashlib.sha256(binary).hexdigest()
        index = by_digest.get(digest)
        if index is None:
            index = len(media)
            by_digest[digest] = index
            media.append({"sha256": digest, "bytes": binary})
        else:
            _require(media[index]["bytes"] == binary, "Hook media digest collision")
        for i, j, part in positions:
            part["image_base64"] = None
            refs.append([i, j, index])
    if not refs:
        return ordinary
    refs.sort(key=lambda ref: (ref[0], ref[1]))
    projected = codec.encode({_TAG: 1, "envelope": raw, "refs": refs, "media": media})
    # Tiny images may cost more in a table. Preserve legacy behavior in that case.
    return projected if len(projected) < len(ordinary) and len(projected) <= MAX_FRAME_SIZE else ordinary


def decode_frame(codec: Codec, data: bytes, enabled: bool, *,
                 incoming_requests: bool, pending: Mapping[int, HookContext] | None = None) -> Envelope:
    """Restore before dispatch; response contexts come ONLY from local pending calls."""
    header = None
    try:
        _bounded(len(data))
        raw = codec.decode(data)
        if _TAG not in raw:
            return Envelope.model_validate(raw)
        projected = raw.get("envelope")
        if isinstance(projected, dict):
            # Do not retain attacker-controlled payload in an exception/header.
            header = Envelope.model_validate({**projected, "payload": {}, "error": None})
        _require(enabled, "Hook media was not negotiated")
        _require(set(raw) == {_TAG, "envelope", "refs", "media"} and type(raw[_TAG]) is int and raw[_TAG] == 1,
                 "Unsupported Hook media wrapper")
        _require(header is not None, "Missing Hook media routing header")
        envelope = Envelope.model_validate(projected)
        if incoming_requests:
            context = request_context(envelope)
            _require(envelope.is_request(), "Unexpected Hook media direction")
        else:
            context = (pending or {}).get(envelope.request_id)
            _require(envelope.is_response(), "Unexpected Hook media direction")
        _require(context is not None and context.matches(envelope), "Uncorrelated Hook media frame")
        items = _items(projected, context)
        _require(items is not None, "Unknown Hook media ContextItem schema")
        refs, media = raw["refs"], raw["media"]
        _require(isinstance(refs, list) and isinstance(media, list) and bool(refs), "Invalid Hook media lists")
        # A legacy image occurrence requires at least a Base64 char; no new
        # arbitrary per-image or image-count restrictions on existing requests.
        _bounded(len(refs))
        _bounded(len(media))
        table_bytes, digests = 0, set()
        for entry in media:
            _require(isinstance(entry, dict) and set(entry) == {"sha256", "bytes"}, "Invalid media entry")
            digest, binary = entry["sha256"], entry["bytes"]
            _require(isinstance(digest, str) and isinstance(binary, bytes) and bool(binary), "Invalid media types")
            table_bytes += len(binary)
            _bounded(table_bytes)
            _require(hashlib.sha256(binary).hexdigest() == digest, "Hook media digest mismatch")
            _require(digest not in digests, "Duplicate Hook media digest")
            digests.add(digest)
        slots = {(i, j): part for i, j, part in _slots(items)}
        positions, used, occurrence_bytes = set(), set(), 0
        expanded = len(codec.encode(projected))
        for ref in refs:
            _require(isinstance(ref, list) and len(ref) == 3 and all(type(v) is int for v in ref), "Invalid media ref")
            i, j, index = ref
            _require((i, j) in slots and (i, j) not in positions, "Invalid media ref position")
            _require(0 <= index < len(media), "Unknown media ref")
            _require(slots[(i, j)].get("image_base64", "missing") is None, "Media placeholder conflict")
            positions.add((i, j))
            used.add(index)
            size = len(media[index]["bytes"])
            occurrence_bytes += size
            _bounded(occurrence_bytes)
            length = 4 * ((size + 2) // 3)
            prefix = 1 if length < 32 else 2 if length <= 255 else 3 if length <= 65535 else 5
            expanded += length + prefix - 1  # Replace MsgPack nil with string.
            _bounded(expanded)  # BEFORE allocating any restored Base64 strings.
        _require(used == set(range(len(media))), "Unused Hook media entry")
        encoded = [base64.b64encode(entry["bytes"]).decode("ascii") for entry in media]
        for i, j, index in refs:
            slots[(i, j)]["image_base64"] = encoded[index]
        # Exact MsgPack size was checked before allocation. Only nil -> ASCII
        # strings changed, so re-encoding the entire restored 32 MiB Envelope
        # here would add another large buffer/CPU copy without a stronger bound.
        return Envelope.model_validate(projected)
    except MediaWireError as exc:
        exc.header = header
        raise
    except Exception as exc:
        raise MediaWireError("Invalid RPC/Hook media frame", header) from exc
