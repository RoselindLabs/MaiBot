"""Explicit pytest result gate; deny network except Windows' stdlib socketpair."""

from pathlib import Path

import ipaddress
import os
import sys


_collected = set()
_completed = set()
_skipped = []
REQUIRED = {
    "pytests/neko/test_reply_contracts.py",
    "pytests/neko/test_life_compaction.py",
    "pytests/neko/test_source_policy.py",
    "pytests/neko/test_plugin_bridge.py",
    "pytests/neko/test_tool_context.py",
    "pytests/neko/test_runtime_preparation.py",
    "pytests/neko/test_release_1318.py",
    "pytests/neko/test_release_1319.py",
    "pytests/neko/test_gemini_failure_diagnostics.py",
    "pytests/neko/test_rpc_frame_limit.py",
    "pytests/neko/test_internal_history_pressure.py",
    "pytests/neko/test_hook_media_wire.py",
    "pytests/neko/test_gif_static_image.py",
    "pytests/maisaka/test_context_compaction.py",
    "pytests/maisaka/test_focus_group_runtime.py",
    "pytests/maisaka/test_replyer_expression_selection.py",
    "pytests/maisaka/test_planner_text_reply.py",
    "pytests/plugin_runtime/test_reply_extension_platform_scope.py",
    "pytests/plugin_runtime/test_config_widgets.py",
    "pytests/plugin_runtime/test_internal_chat_capability.py",
    "pytests/plugin_runtime/test_message_route_identity.py",
    "pytests/config_test/test_startup_bindings.py",
    "pytests/maisaka/test_behavior_style_prompt.py",
    "tests/test_personality_service.py",
    "pytests/maisaka/test_context_preparation_status.py",
    "pytests/maisaka/test_tool_monitor_condensed_result.py",
    "pytests/plugin_runtime/test_desktop_pet_buttons.py",
    "pytests/plugin_runtime/test_desktop_pet_capability.py",
    "pytests/plugin_runtime/test_desktop_pet_drag.py",
    "pytests/plugin_runtime/test_desktop_pet_hooks.py",
    "pytests/plugin_runtime/test_market_capabilities.py",
    "pytests/startup_test/test_self_update_dependency_sources.py",
    "pytests/webui/test_plugin_sources.py",
    "pytests/prompt_test/test_prompt_i18n.py",
}
if os.environ.get("NEKO_RETIRED_LIFE_TESTS") == "1":
    REQUIRED.add("pytests/neko_retired/test_life_bridge.py")


def _network_guard(event, args):
    if event != "socket.connect":
        return
    address = args[1]
    if isinstance(address, tuple):
        try:
            caller = sys._getframe(1)
            if (
                ipaddress.ip_address(address[0]).is_loopback
                and caller.f_code.co_name == "_fallback_socketpair"
                and caller.f_globals.get("__name__") == "socket"
                and caller.f_locals.get("csock") is args[0]
                and address == (caller.f_locals.get("addr"), caller.f_locals.get("port"))
            ):
                return
        except ValueError:
            pass
    raise RuntimeError("External network disabled in synthetic fork tests")


# Windows asyncio uses an internal loopback socket pair; allow it, not suppliers.
sys.addaudithook(_network_guard)


def pytest_collection_finish(session):
    _collected.update(item.nodeid for item in session.items)


def pytest_runtest_logreport(report):
    if report.skipped:
        _skipped.append(report.nodeid)
    if report.when == "call" and report.passed:
        _completed.add(report.nodeid)


def contracts_complete(collected, completed, skipped):
    paths = {identity.split("::", 1)[0] for identity in completed}
    return bool(collected and not skipped and REQUIRED <= paths and completed == collected)


def pytest_sessionfinish(session, exitstatus):
    if not contracts_complete(_collected, _completed, _skipped):
        session.exitstatus = 1
        print("Fork contract gate failed: missing group, incomplete execution or unexpected skip", file=sys.stderr)


def pytest_report_header():
    from importlib.metadata import version
    import maibot_sdk

    return f"Real SDK {version('maibot-plugin-sdk')} at {Path(maibot_sdk.__file__).name}; synthetic instance only"
