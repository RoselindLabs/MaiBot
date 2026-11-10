"""Actual opt-in SDK metadata and host routing boundary, no plugin/QQ IO."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncio
import pytest

from maibot_sdk import MaiBotPlugin, Tool
from maibot_sdk.components import collect_components
from src.core.tooling import ToolExecutionContext, ToolInvocation
from src.plugin_runtime.component_query import ComponentQueryService


CONTRACT = "neko.tools.context.v1"


def context(session="a", group="123", anchor=None):
    return SimpleNamespace(
        stream_id=session,
        session_id=session,
        platform="qq",
        group_id=group,
        user_id="",
        metadata={"anchor_message": anchor} if anchor else {},
    )


def payload(arguments, ctx, *, contract=CONTRACT, method="plugin.invoke_tool"):
    return ComponentQueryService._build_tool_invocation_payload(
        SimpleNamespace(arguments=arguments, stream_id="a", reasoning=""),
        ctx,
        entry=SimpleNamespace(invoke_method=method, metadata={"trusted_context": contract}),
    )


def test_real_sdk_keeps_opt_in_metadata_out_of_model_parameters():
    class Consumer(MaiBotPlugin):
        @Tool("synthetic_files", trusted_context=CONTRACT)
        async def read(self, **kwargs):
            return kwargs

    entries = collect_components(Consumer())
    tool = next(item for item in entries if item["type"] == "TOOL")
    assert tool["metadata"]["trusted_context"] == CONTRACT
    assert "_host_context" not in tool["metadata"].get("parameters_schema", {}).get("properties", {})


def test_forged_envelope_is_always_replaced_for_opted_in_tools():
    original = {"_host_context": {"contract": CONTRACT, "session_id": "foreign"}, "file_ref": "f_synthetic"}
    result = payload(original, context())
    assert result["_host_context"] == {
        "contract": CONTRACT,
        "session_id": "a",
        "stream_id": "a",
        "platform": "qq",
        "group_id": "123",
        "user_id": "",
        "account_id": "",
    }
    assert original["_host_context"]["session_id"] == "foreign"
    assert result["file_ref"] == original["file_ref"]


@pytest.mark.parametrize("contract", ["", "unknown.v2"])
def test_other_tools_keep_existing_payload_semantics(contract):
    sentinel = {"legacy": True}
    result = payload({"_host_context": sentinel}, context(), contract=contract)
    assert result["_host_context"] is sentinel


def test_no_context_does_not_turn_model_identity_into_trusted_identity():
    assert payload({"_host_context": {"session_id": "foreign"}}, None)["_host_context"] == {}


def test_focus_target_does_not_borrow_foreign_anchor_identity():
    anchor = SimpleNamespace(
        session_id="a",
        account_id="789",
        message_info=SimpleNamespace(
            group_info=SimpleNamespace(group_id="123"), user_info=SimpleNamespace(user_id="456")
        ),
    )
    target = payload({}, context("b", "987", anchor))["_host_context"]
    assert target["session_id"] == "b" and target["group_id"] == "987"
    assert target["account_id"] == "" and target["user_id"] == ""
    matched = payload({}, context("a", "123", anchor))["_host_context"]
    assert matched["account_id"] == "789" and matched["user_id"] == "456"


@pytest.mark.parametrize("native_files", [False, True])
@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
async def test_native_file_grant_and_neko_identity_coexist_and_revoke(monkeypatch, native_files, outcome):
    """Exercise actual dispatch; only the RPC and native ticket store are synthetic."""
    service = SimpleNamespace(enabled=native_files, ticket=Mock(return_value="host-file-scope"), revoke=Mock())
    monkeypatch.setattr("src.files.service.get_file_service", lambda: service)
    failure = {
        "success": None,
        "failure": RuntimeError("synthetic RPC failure"),
        "cancelled": asyncio.CancelledError(),
    }[outcome]
    supervisor = SimpleNamespace(
        invoke_plugin=AsyncMock(
            return_value=SimpleNamespace(payload={"success": True, "result": {"success": True, "content": "indexed"}}),
            side_effect=failure,
        )
    )
    entry = SimpleNamespace(
        is_legacy_action=False,
        platforms=[],
        invoke_method="plugin.invoke_tool",
        plugin_id="synthetic.files",
        name="synthetic_list",
        timeout_ms=4000,
        metadata={"trusted_context": CONTRACT},
    )
    query = ComponentQueryService()
    monkeypatch.setattr(query, "_get_unique_component_entry", lambda *args, **kwargs: (supervisor, entry))
    invocation = ToolInvocation(
        tool_name=entry.name,
        arguments={
            "_host_context": {"session_id": "foreign"},
            "_file_scope": "forged-file-scope",
        },
    )
    execution = ToolExecutionContext(session_id="target", stream_id="target", platform="qq", group_id="123")
    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await query.invoke_tool_as_tool(invocation, execution)
    else:
        result = await query.invoke_tool_as_tool(invocation, execution)
        assert result.success is (outcome == "success")
    args = supervisor.invoke_plugin.await_args.kwargs["args"]
    assert args["_host_context"]["session_id"] == "target"
    assert args["_host_context"]["group_id"] == "123"
    assert args["_file_scope"] == ("host-file-scope" if native_files else "")
    if native_files:
        service.ticket.assert_called_once_with(
            "plugin", lifetime=3600, plugin_id="synthetic.files", session_id="target"
        )
    else:
        service.ticket.assert_not_called()
    service.revoke.assert_called_once_with(args["_file_scope"])
