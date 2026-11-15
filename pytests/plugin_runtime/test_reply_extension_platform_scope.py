"""回复扩展的平台范围同时约束声明展示、准备和发送前处理。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from maibot_sdk import ReplyExtension
from maibot_sdk.components import collect_components
import pytest

from src.core.tooling import ToolAvailabilityContext
from src.plugin_runtime import integration
from src.plugin_runtime.host.component_registry import ComponentRegistry
from src.plugin_runtime.host.reply_extensions import (
    ReplyExtensionExecution,
    build_reply_extensions_schema,
    get_reply_extensions,
)
from src.plugin_runtime.protocol.envelope import ComponentDeclaration
from src.plugin_runtime.runner.plugin_loader import PluginMeta
from src.plugin_runtime.runner.runner_main import PluginRunner


def registry(platforms):
    result = ComponentRegistry()
    schema = {"type": "object", "properties": {"caption": {"type": "string", "default": ""}}}
    result.register_plugin_components("test", [
        ComponentDeclaration(
            name="restricted", component_type="REPLY_EXTENSION", plugin_id="test",
            platforms=platforms, metadata={"parameters_schema": schema},
        ).model_dump(),
        {"name": "unlimited", "component_type": "REPLY_EXTENSION", "metadata": {"parameters_schema": schema}},
    ])
    return result


@pytest.mark.parametrize("declaration", ["telegram", ["telegram", "qq"], None, []])
@pytest.mark.parametrize("platform", ["telegram", "qq", "webui", ""])
def test_reply_schema_filters_platform(monkeypatch, declaration, platform):
    declared = {declaration} if isinstance(declaration, str) else set(declaration or [])
    supervisor = SimpleNamespace(component_registry=registry(declaration))
    monkeypatch.setattr(integration, "get_plugin_runtime_manager", lambda: SimpleNamespace(supervisors=[supervisor]))
    schema = build_reply_extensions_schema(ToolAvailabilityContext(platform=platform))
    assert "test.unlimited" in schema["properties"]
    assert ("test.restricted" in schema["properties"]) == (not declared or platform in declared)
    assert "platforms" not in schema["properties"]["test.unlimited"]["properties"]


@pytest.mark.parametrize("invalid", [1, True, {}, "", " ", ["telegram", None], [""], [2]])
def test_invalid_platforms_fail_registration(invalid):
    with pytest.raises(ValueError, match="platforms"):
        registry(invalid)


def test_normalization_and_management_inventory(monkeypatch):
    result = registry([" Telegram ", "TELEGRAM", "qq"])
    entry = next(entry for entry in result.get_components_by_type("REPLY_EXTENSION") if entry.name == "restricted")
    assert entry.platforms == {"telegram", "qq"}
    supervisor = SimpleNamespace(component_registry=result)
    monkeypatch.setattr(integration, "get_plugin_runtime_manager", lambda: SimpleNamespace(supervisors=[supervisor]))
    assert set(get_reply_extensions()) == {"test.restricted", "test.unlimited"}
    schema = build_reply_extensions_schema(ToolAvailabilityContext(platform=" TELEGRAM "))
    assert "test.restricted" in schema["properties"]


@pytest.mark.asyncio
@pytest.mark.parametrize("declaration_style", ["sdk", "legacy_metadata"])
async def test_sdk_declaration_survives_wire_protocol_and_filters_reply_schema(monkeypatch, tmp_path, declaration_style):
    class Plugin:
        def get_components(self):
            components = collect_components(self)
            if declaration_style == "legacy_metadata":
                for component in components:
                    for field in ("platforms", "chat_scope", "allowed_session"):
                        if field in component:
                            component["metadata"][field] = component.pop(field)
            return components

        @ReplyExtension("forward_images", platforms=" QQ ", parameters={"caption": {"type": "string"}})
        async def forward_images(self, **kwargs):
            return {}

    manifest = SimpleNamespace(
        name="Test", version="1.0.0", plugin_type="extension", capabilities=[],
        plugin_dependency_ids=[], llm_provider_client_types=[],
    )
    meta = PluginMeta("test", "test", str(tmp_path), Plugin(), manifest)
    registration = await PluginRunner("unused", "test", [])._build_plugin_registration_payload(meta)
    declaration = registration.components[0]
    wire_declaration = ComponentDeclaration.model_validate_json(declaration.model_dump_json())
    assert wire_declaration.platforms == ["qq"]
    assert {"platforms", "chat_scope", "allowed_session"}.isdisjoint(wire_declaration.metadata)
    result = ComponentRegistry()
    result.register_plugin_components("test", [wire_declaration.model_dump()])
    supervisor = SimpleNamespace(component_registry=result)
    monkeypatch.setattr(integration, "get_plugin_runtime_manager", lambda: SimpleNamespace(supervisors=[supervisor]))
    schema = build_reply_extensions_schema(ToolAvailabilityContext(platform="qq"))
    assert set(schema["properties"]) == {"test.forward_images"}
    assert set(schema["properties"]["test.forward_images"]["properties"]) == {"caption"}
    assert build_reply_extensions_schema(ToolAvailabilityContext(platform="telegram")) is None


def test_platform_scope_combines_with_chat_and_session_scope():
    result = ComponentRegistry()
    result.register_component(
        "group_extension", "REPLY_EXTENSION", "test",
        {"parameters_schema": {"type": "object", "properties": {}}},
        chat_scope="group", allowed_session=["session-1"], platforms=["telegram", "qq"],
    )
    for platform, group, session, expected in [
        ("telegram", True, "session-1", True), ("qq", True, "session-1", True),
        ("webui", True, "session-1", False), ("telegram", False, "session-1", False),
        ("telegram", True, "session-2", False),
    ]:
        assert bool(result.get_components_by_type(
            "REPLY_EXTENSION", platform=platform, is_group_chat=group, session_id=session,
        )) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["", "qq", "telegram"])
async def test_prepare_rejects_other_platforms_before_plugin_invocation(monkeypatch, platform):
    supervisor = SimpleNamespace(
        component_registry=registry("telegram"),
        invoke_plugin=AsyncMock(return_value=SimpleNamespace(error=None, payload={"success": True, "result": {}})),
    )
    monkeypatch.setattr(integration, "get_plugin_runtime_manager", lambda: SimpleNamespace(supervisors=[supervisor]))
    execution = ReplyExtensionExecution(
        ToolAvailabilityContext(platform=platform), reply_message_id="message", call_id="call",
    )
    if platform != "telegram":
        with pytest.raises(ValueError, match="不适用于当前聊天"):
            await execution.prepare({"test.restricted": {}})
        supervisor.invoke_plugin.assert_not_awaited()
        return
    await execution.prepare({"test.restricted": {}})
    messages = [{"segments": [{"type": "text", "data": "reply"}], "quote_previous": False}]
    assert await execution.transform("reply", messages) == messages
    assert supervisor.invoke_plugin.await_count == 2
    assert supervisor.invoke_plugin.call_args.args[3]["chat"]["platform"] == "telegram"


@pytest.mark.asyncio
async def test_before_send_rechecks_platform_scope(monkeypatch):
    result = registry("telegram")
    supervisor = SimpleNamespace(
        component_registry=result,
        invoke_plugin=AsyncMock(return_value=SimpleNamespace(error=None, payload={"success": True, "result": {}})),
    )
    monkeypatch.setattr(integration, "get_plugin_runtime_manager", lambda: SimpleNamespace(supervisors=[supervisor]))
    execution = ReplyExtensionExecution(
        ToolAvailabilityContext(platform="telegram"), reply_message_id="message", call_id="call",
    )
    await execution.prepare({"test.restricted": {}})
    entry = next(entry for entry in result.get_components_by_type("REPLY_EXTENSION") if entry.name == "restricted")
    entry.platforms = {"qq"}
    with pytest.raises(ValueError, match="已停用、卸载或重新加载"):
        await execution.transform("reply", [{"segments": [{"type": "text", "data": "reply"}], "quote_previous": False}])
    assert supervisor.invoke_plugin.await_count == 1
