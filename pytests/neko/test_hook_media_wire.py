"""Real MsgPack/Envelope and two-process IPC, synthetic pixels only.

The child allows only its exact UDS endpoint (Windows uses Named Pipe); no model,
QQ, production files or public network. A child is not an agent or plugin host.
"""

from pathlib import Path

import asyncio
import ast
import base64
import copy
import io
import os
import random
import subprocess
import sys

import pytest
from PIL import Image

from src.plugin_runtime.protocol.codec import Codec, MsgPackCodec
from src.plugin_runtime.protocol.envelope import Envelope, HelloPayload, HelloResponsePayload, MessageType
from src.plugin_runtime.protocol.errors import ErrorCode, RPCError
from src.plugin_runtime.protocol.hook_media import (
    MediaWireError,
    decode_frame,
    encode_frame,
    request_context,
)
from src.plugin_runtime.transport.base import MAX_FRAME_SIZE


def picture(seed=1, side=128):
    stream = io.BytesIO()
    Image.frombytes("RGBA", (side, side), random.Random(seed).randbytes(side * side * 4)).save(stream, format="PNG")
    return stream.getvalue()


def image_item(raw, index=0):
    return {
        "item_type": "UserMessageItem",
        "meta": {
            "item_id": f"synthetic-{index}",
            "logical_turn_id": None,
            "timestamp": "2026-11-02T00:00:00",
        },
        "parts": [
            {"type": "text", "text": f"own synthetic image {index}"},
            {
                "type": "image",
                "image_format": "png",
                "image_base64": base64.b64encode(raw).decode("ascii"),
                "private_ref": "user-owned-extra",
            },
        ],
    }


def request(items=None, hook="maisaka.planner.before_request"):
    return Envelope(
        request_id=3,
        message_type=MessageType.REQUEST,
        method="plugin.invoke_hook",
        plugin_id="synthetic.plugin",
        payload={
            "component_name": "synthetic_hook",
            "args": {
                "hook_name": hook,
                "items": items or [],
                "item_schema_version": 1,
                "session_id": "",
                "tool_definitions": [{"type": "function", "function": {"name": "reply", "description": "before"}}],
                "extra": {"image_base64": "do not decode", "refs": ["user value"]},
            },
        },
    )


def repeated_request():
    raw = picture()
    return request([image_item(raw, i) for i in range(2)])


CODEC = MsgPackCodec()


def roundtrip(envelope, context=None, enabled=True):
    before = envelope.model_dump()
    ctx = context or request_context(envelope)
    wire = encode_frame(CODEC, envelope, enabled, context)
    restored = decode_frame(
        CODEC, wire, enabled, incoming_requests=envelope.is_request(), pending={ctx.request_id: ctx} if ctx else {}
    )
    assert restored.model_dump() == before
    assert envelope.model_dump() == before
    return wire, restored


@pytest.mark.parametrize("same", [False, True])
@pytest.mark.parametrize("hook", ["maisaka.planner.before_request", "maisaka.replyer.before_model_request"])
def test_four_images_both_directions_preserve_every_occurrence(same, hook):
    raws = [picture(0 if same else i) for i in range(4)]
    req = request([image_item(raw, i) for i, raw in enumerate(raws)], hook)
    wire, _ = roundtrip(req)
    wrapper = CODEC.decode(wire)
    assert len(wrapper["media"]) == (1 if same else 4)
    assert [ref[:2] for ref in wrapper["refs"]] == [[i, 1] for i in range(4)]
    kwargs = copy.deepcopy(req.payload["args"])
    kwargs["tool_definitions"][0]["function"]["description"] = "changed, media untouched"
    _, restored = roundtrip(req.make_response({"modified_kwargs": kwargs}), request_context(req))
    assert set(restored.payload["modified_kwargs"]) == set(req.payload["args"])
    assert restored.payload["modified_kwargs"]["items"] == req.payload["args"]["items"]


def test_noncanonical_literal_and_unknown_fields_are_not_recursively_transformed():
    req = repeated_request()
    args = req.payload["args"]
    args.update(refs=["user"], media={"a": "user"}, envelope="user", hook_media_wire="user")
    for text in ["Zh==", "Zg===", " Zg==", "Zg==\n", "图", "", "not@base64"]:
        value = image_item(picture(), len(args["items"]))
        value["parts"][1]["image_base64"] = text
        args["items"].extend([value, copy.deepcopy(value)])
    args["items"].append({"item_type": "Unknown", "parts": [{"type": "image", "image_base64": "opaque"}]})
    args["messages"] = [{"role": "user", "content": "legacy arbitrary field"}]
    wire, _ = roundtrip(req)
    assert len(CODEC.decode(wire)["refs"]) == 2


def test_unique_images_use_binary_each_processed_once(monkeypatch):
    import src.plugin_runtime.protocol.hook_media as module

    raws = [picture(i) for i in range(4)]
    req = request([image_item(raw, i) for i, raw in enumerate(raws)])
    calls, hashes = [], []
    canonical, sha256 = module._canonical, module.hashlib.sha256

    def counted(text):
        calls.append(text)
        return canonical(text)

    def counted_hash(raw):
        hashes.append(len(raw))
        return sha256(raw)

    monkeypatch.setattr(module, "_canonical", counted)
    monkeypatch.setattr(module.hashlib, "sha256", counted_hash)
    kwargs = copy.deepcopy(req.payload["args"])
    kwargs["tool_definitions"][0]["function"]["description"] = "after"
    response = req.make_response({"modified_kwargs": kwargs})
    for envelope in (req, response):
        calls.clear()
        hashes.clear()
        wire = encode_frame(CODEC, envelope, True, request_context(req))
        assert calls == [base64.b64encode(raw).decode("ascii") for raw in raws]
        assert hashes == [len(raw) for raw in raws]
        assert len(CODEC.decode(wire)["media"]) == 4
        assert len(wire) < len(CODEC.encode_envelope(envelope))
        restored = decode_frame(
            CODEC, wire, True, incoming_requests=envelope.is_request(), pending={req.request_id: request_context(req)}
        )
        assert restored.model_dump() == envelope.model_dump()


def test_all_media_processed_once_per_unique_base64(monkeypatch):
    import src.plugin_runtime.protocol.hook_media as module

    repeated, unique = picture(), picture(2)
    req = request([image_item(repeated, 0), image_item(unique, 1), image_item(repeated, 2)])
    calls, hashes = [], []
    canonical, sha256 = module._canonical, module.hashlib.sha256

    def counted(text):
        calls.append(text)
        return canonical(text)

    def counted_hash(raw):
        hashes.append(len(raw))
        return sha256(raw)

    monkeypatch.setattr(module, "_canonical", counted)
    monkeypatch.setattr(module.hashlib, "sha256", counted_hash)
    wire = encode_frame(CODEC, req, True)
    wrapper = CODEC.decode(wire)
    assert calls == [base64.b64encode(raw).decode("ascii") for raw in (repeated, unique)]
    assert hashes == [len(repeated), len(unique)]
    assert len(wrapper["media"]) == 2
    assert wrapper["refs"] == [[0, 1, 0], [1, 1, 1], [2, 1, 0]]
    assert all(part["parts"][1]["image_base64"] is None for part in wrapper["envelope"]["payload"]["args"]["items"])
    restored = decode_frame(CODEC, wire, True, incoming_requests=True)
    assert restored.model_dump() == req.model_dump()


def test_repeated_noncanonical_only_is_literal_and_tiny_table_falls_back():
    req = repeated_request()
    for value in req.payload["args"]["items"]:
        value["parts"][1]["image_base64"] = "Zh=="
    assert encode_frame(CODEC, req, True) == CODEC.encode_envelope(req)
    for count in (1, 2):
        req = request([image_item(b"f", i) for i in range(count)])
        assert encode_frame(CODEC, req, True) == CODEC.encode_envelope(req)


def test_no_media_and_legacy_fallback_have_identical_wire():
    req = request([])
    assert encode_frame(CODEC, req, True) == CODEC.encode_envelope(req)
    req = request([image_item(picture())])
    assert encode_frame(CODEC, req, False) == CODEC.encode_envelope(req)
    for env in [req.make_response({"success": True}), request([image_item(picture())], "other.hook")]:
        assert encode_frame(CODEC, env, True) == CODEC.encode_envelope(env)
    assert (
        HelloPayload.model_validate(
            {"runner_id": "old", "sdk_version": "2.20", "session_token": "x"}
        ).transport_capabilities
        == []
    )
    assert HelloResponsePayload.model_validate({"accepted": True}).transport_capabilities == []


def test_add_delete_and_reorder_images():
    req = request([image_item(picture())])
    kwargs = copy.deepcopy(req.payload["args"])
    kwargs["items"] = [image_item(picture(2), 2), kwargs["items"][0]]
    roundtrip(req.make_response({"modified_kwargs": kwargs}), request_context(req))
    kwargs["items"].pop()
    roundtrip(req.make_response({"modified_kwargs": kwargs}), request_context(req))
    kwargs["items"] = []
    roundtrip(req.make_response({"modified_kwargs": kwargs}), request_context(req))


def test_real_host_replay_helper_retains_unchanged_and_invalidates_only_edit():
    from src.llm_models.payload_content.context_item import (
        CONTEXT_ITEM_SCHEMA_VERSION,
        AssistantMessageItem,
        ContextImagePart,
        ContextItemMeta,
        ContextTextPart,
        ProviderReplayFragment,
        ProviderScope,
    )
    from src.llm_models.payload_content.context_protocol import ContextProtocolMode, validate_context_items
    from src.llm_models.payload_content.context_snapshot import (
        deserialize_context_item_snapshot,
        serialize_context_item_snapshot,
    )

    path = Path(__file__).resolve().parents[2] / "src/plugin_runtime/hook_payloads.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    function = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "deserialize_prompt_items"
    )
    namespace = {
        "CONTEXT_ITEM_SCHEMA_VERSION": CONTEXT_ITEM_SCHEMA_VERSION,
        "ContextProtocolMode": ContextProtocolMode,
        "serialize_context_item_snapshot": serialize_context_item_snapshot,
        "deserialize_context_item_snapshot": deserialize_context_item_snapshot,
        "validate_context_items": validate_context_items,
    }
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), function],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    helper = namespace["deserialize_prompt_items"]
    scope = ProviderScope(1, "synthetic", "synthetic", "synthetic", "synthetic")
    replay = ProviderReplayFragment.from_payload(scope, {"synthetic": "private native payload never transported"})
    originals = [
        AssistantMessageItem(
            ContextItemMeta.create(item_id=str(i)),
            (ContextTextPart("before"), ContextImagePart("png", base64.b64encode(picture()).decode())),
            replay=replay,
        )
        for i in range(2)
    ]
    req = request([serialize_context_item_snapshot(value) for value in originals])
    kwargs = copy.deepcopy(req.payload["args"])
    kwargs["tool_definitions"][0]["function"]["description"] = "after"
    _, restored = roundtrip(req.make_response({"modified_kwargs": kwargs}), request_context(req))
    result = helper(restored.payload["modified_kwargs"]["items"], original_items=originals)
    assert all(result[i] is originals[i] for i in range(2))
    kwargs["items"][0]["parts"][0]["text"] = "real edit"
    _, restored = roundtrip(req.make_response({"modified_kwargs": kwargs}), request_context(req))
    result = helper(restored.payload["modified_kwargs"]["items"], original_items=originals)
    assert result[0].replay is None
    assert result[1] is originals[1] and result[1].replay is replay


@pytest.mark.parametrize(
    "attack",
    [
        "hash",
        "bytes",
        "unknown",
        "position",
        "duplicate_ref",
        "duplicate_hash",
        "inconsistent_hash",
        "placeholder",
        "unused",
        "version",
        "schema",
    ],
)
def test_invalid_sidecar_rejected_before_dispatch(attack):
    req = repeated_request()
    wrapper = CODEC.decode(encode_frame(CODEC, req, True))
    if attack == "hash":
        wrapper["media"][0]["sha256"] = "0" * 64
    elif attack == "bytes":
        wrapper["media"][0]["bytes"] = b"bad"
    elif attack == "unknown":
        wrapper["refs"][0][2] = 9
    elif attack == "position":
        wrapper["refs"][0][1] = 0
    elif attack == "duplicate_ref":
        wrapper["refs"].append(wrapper["refs"][0])
    elif attack in {"duplicate_hash", "inconsistent_hash", "unused"}:
        entry = copy.deepcopy(wrapper["media"][0])
        if attack == "inconsistent_hash":
            entry["bytes"] = b"different"
        wrapper["media"].append(entry)
        if attack == "unused":
            wrapper["refs"].clear()
    elif attack == "placeholder":
        wrapper["envelope"]["payload"]["args"]["items"][0]["parts"][1]["image_base64"] = "literal"
    elif attack == "version":
        wrapper["hook_media_wire"] = 99
    else:
        wrapper["envelope"]["payload"]["args"]["item_schema_version"] = 99
    with pytest.raises(MediaWireError):
        decode_frame(CODEC, CODEC.encode(wrapper), True, incoming_requests=True)


def test_negotiation_and_trusted_pending_response_association_required():
    req = repeated_request()
    ctx = request_context(req)
    wire = encode_frame(CODEC, req, True)
    with pytest.raises(MediaWireError, match="not negotiated"):
        decode_frame(CODEC, wire, False, incoming_requests=True)
    response = req.make_response({"modified_kwargs": req.payload["args"]})
    wire = encode_frame(CODEC, response, True, ctx)
    with pytest.raises(MediaWireError, match="Uncorrelated"):
        decode_frame(CODEC, wire, True, incoming_requests=False, pending={})
    wrapper = CODEC.decode(wire)
    wrapper["envelope"]["plugin_id"] = "wrong.plugin"
    with pytest.raises(MediaWireError, match="Uncorrelated"):
        decode_frame(CODEC, CODEC.encode(wrapper), True, incoming_requests=False, pending={3: ctx})


def test_wire_and_actual_expanded_32mib_caps_no_new_image_subcaps():
    req = repeated_request()
    req.payload["args"]["padding"] = ""
    length = MAX_FRAME_SIZE + 1 - len(CODEC.encode_envelope(req)) - 4
    req.payload["args"]["padding"] = "x" * length
    assert len(CODEC.encode_envelope(req)) == MAX_FRAME_SIZE + 1
    with pytest.raises(MediaWireError):
        encode_frame(CODEC, req, True)
    wrapper = CODEC.decode(encode_frame(CODEC, repeated_request(), True))
    wrapper["envelope"]["payload"]["args"]["padding"] = req.payload["args"]["padding"]
    wire = CODEC.encode(wrapper)
    assert len(wire) < MAX_FRAME_SIZE
    with pytest.raises(MediaWireError):
        decode_frame(CODEC, wire, True, incoming_requests=True)
    with pytest.raises(MediaWireError):
        decode_frame(CODEC, b"x" * (MAX_FRAME_SIZE + 1), True, incoming_requests=True)
    # >64 image occurrences accepted; no prototype 64/8MiB restrictions.
    roundtrip(request([image_item(picture(), i) for i in range(65)]))
    roundtrip(request([image_item(b"x" * (8 * 1024 * 1024 + 1))]))


class LegacyCodec(Codec):
    """Use old wire only, simulating a peer that advertises no capability."""

    def encode(self, obj):
        return CODEC.encode(obj)

    def decode(self, data):
        return CODEC.decode(data)

    def encode_envelope(self, envelope):
        raw = envelope.model_dump()
        if raw["method"] == "runner.hello":
            raw["payload"].pop("transport_capabilities", None)
        return CODEC.encode(raw)

    def decode_envelope(self, data):
        return CODEC.decode_envelope(data)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "new",
        "old-runner",
        "old-host",
        "corrupt-once",
        "unnegotiated",
        "uncorrelated",
        "corrupt-request",
        "reconnect",
        "damaged",
    ],
)
async def test_two_process_real_rpc_ipc_and_negotiation(tmp_path, mode):
    from src.plugin_runtime.host.rpc_server import RPCServer
    from src.plugin_runtime.transport.factory import create_transport_server

    class CorruptRequestOnce(MsgPackCodec):
        corrupt = True

        def encode(self, obj):
            if self.corrupt and "hook_media_wire" in obj:
                obj["media"][0]["sha256"] = "0" * 64
                self.corrupt = False
            return super().encode(obj)

    server = RPCServer(
        create_transport_server(),
        session_token="synthetic-token",
        codec=LegacyCodec() if mode == "old-host" else CorruptRequestOnce() if mode == "corrupt-request" else None,
        host_version="synthetic",
    )
    await server.start()
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).resolve().parents[2]),
        MAIBOT_INSTANCE_DIR=str(tmp_path),
        PYTHONDONTWRITEBYTECODE="1",
    )
    child = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--runner", server._transport.get_address(), mode],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        # Poll only local connection readiness, bounded by wait_for, not remote work.
        async def ready():
            while not server.is_connected:
                if child.poll() is not None:
                    raise AssertionError(child.communicate()[1].decode(errors="replace"))
                await asyncio.sleep(0.01)

        await asyncio.wait_for(ready(), 15)
        assert server._hook_media_enabled == (
            mode in {"new", "corrupt-once", "uncorrelated", "corrupt-request", "reconnect", "damaged"}
        )
        req = repeated_request()
        if mode in {"corrupt-once", "unnegotiated", "uncorrelated", "corrupt-request", "damaged"}:
            if mode == "unnegotiated":
                server._hook_media_enabled = True  # Deliberately violate negotiation.
            with pytest.raises(RPCError) as error:
                await asyncio.wait_for(server.send_request(req.method, req.plugin_id, req.payload, 10000), 3)
            assert error.value.code == ErrorCode.E_BAD_PAYLOAD
            assert not server._pending_requests
            if mode == "unnegotiated":
                server._hook_media_enabled = False
            if mode in {"uncorrelated", "damaged"}:
                return  # Invalid association closes this connection; no retries.
        reply = await server.send_request(req.method, req.plugin_id, req.payload)
        kwargs = reply.payload["modified_kwargs"]
        assert kwargs["items"] == req.payload["args"]["items"]
        assert kwargs["tool_definitions"][0]["function"]["description"] == "after"
        assert kwargs["synthetic_calls"] == (2 if mode == "corrupt-once" else 1)
        assert kwargs["synthetic_media_enabled"] == server._hook_media_enabled
        if mode == "new":
            text_request = request([])
            plain = await server.send_request(text_request.method, text_request.plugin_id, text_request.payload)
            assert plain.payload["modified_kwargs"]["items"] == []
        if mode == "reconnect":
            await server._connection.close()
            await asyncio.to_thread(child.communicate, timeout=10)
            assert child.returncode == 0

            async def released():
                while server._connection is not None:
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(released(), 3)
            assert not server._hook_media_enabled
            child = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--runner",
                    server._transport.get_address(),
                    "old-runner",
                ],
                cwd=tmp_path,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            await asyncio.wait_for(ready(), 15)
            assert not server._hook_media_enabled
            reply = await server.send_request(req.method, req.plugin_id, req.payload)
            assert reply.payload["modified_kwargs"]["items"] == req.payload["args"]["items"]
            assert not reply.payload["modified_kwargs"]["synthetic_media_enabled"]
        await server.stop()
        assert not server._hook_media_enabled
        assert not server._pending_hook_media
        stdout, stderr = await asyncio.to_thread(child.communicate, timeout=10)
        assert child.returncode == 0, (stdout.decode(errors="replace"), stderr.decode(errors="replace"))
    finally:
        await server.stop()
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)


def _child_guard(address):
    def guard(event, args):
        if event != "socket.connect":
            return
        target = args[1]
        if isinstance(target, str) and target == address:
            return  # Exact synthetic UDS only; never arbitrary network tuples.
        caller = sys._getframe(1)
        if (
            caller.f_code.co_name == "_fallback_socketpair"
            and caller.f_globals.get("__name__") == "socket"
            and caller.f_locals.get("csock") is args[0]
            and target == (caller.f_locals.get("addr"), caller.f_locals.get("port"))
        ):
            return
        raise RuntimeError("Network forbidden in synthetic IPC child")

    sys.addaudithook(guard)


async def _runner_child(address, mode):
    from src.plugin_runtime.runner.rpc_client import RPCClient

    class CorruptOnce(MsgPackCodec):
        corrupt = True

        def encode(self, obj):
            if self.corrupt and "hook_media_wire" in obj and obj["envelope"]["message_type"] == "response":
                if mode == "damaged":
                    self.corrupt = False
                    return b"\xc1"
                if mode == "uncorrelated":
                    obj["envelope"]["plugin_id"] = "wrong-plugin"
                else:
                    obj["media"][0]["sha256"] = "0" * 64
                self.corrupt = False
            return super().encode(obj)

    codec = (
        LegacyCodec()
        if mode in {"old-runner", "unnegotiated"}
        else CorruptOnce()
        if mode in {"corrupt-once", "uncorrelated", "damaged"}
        else None
    )
    client = RPCClient(address, "synthetic-token", codec=codec)
    calls = 0

    async def handle(envelope):
        nonlocal calls
        calls += 1
        kwargs = copy.deepcopy(envelope.payload["args"])
        kwargs["tool_definitions"][0]["function"]["description"] = "after"
        kwargs.update(synthetic_calls=calls, synthetic_media_enabled=client._hook_media_enabled)
        return envelope.make_response({"action": "continue", "modified_kwargs": kwargs})

    client.register_method("plugin.invoke_hook", handle)
    assert await client.connect_and_handshake()
    await client._recv_task
    assert not client._hook_media_enabled
    await client.disconnect()


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "--runner":
    _child_guard(sys.argv[2])
    asyncio.run(_runner_child(sys.argv[2], sys.argv[3]))
