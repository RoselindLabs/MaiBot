"""Run bounded fork contracts using an isolated disposable instance directory."""

from pathlib import Path
from tempfile import TemporaryDirectory
from importlib.metadata import version

import os
import argparse
import json
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
TESTS = (
    "pytests/neko",
    "pytests/maisaka/test_context_compaction.py",
    "pytests/maisaka/test_focus_group_runtime.py",
    "pytests/maisaka/test_replyer_expression_selection.py",
    "pytests/maisaka/test_planner_text_reply.py",
    "pytests/plugin_runtime/test_reply_extension_platform_scope.py",
    "pytests/plugin_runtime/test_config_widgets.py",
    "pytests/plugin_runtime/test_internal_chat_capability.py",
    "pytests/plugin_runtime/test_message_route_identity.py::test_formal_identity_roundtrip_and_old_fields_cannot_override",
    "pytests/plugin_runtime/test_message_route_identity.py::test_inbound_rejects_undeclared_identity",
    "pytests/plugin_runtime/test_message_route_identity.py::test_route_rpc_does_not_discover_accounts",
    "pytests/config_test/test_startup_bindings.py",
    "pytests/maisaka/test_behavior_style_prompt.py",
    # v1.3.19: new permissioned APIs, context preparation/trim and update-source fixes.
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
    # v1.3.17-added behavior, not the unrelated historical locale/metadata suite.
    "pytests/prompt_test/test_prompt_i18n.py::test_outdated_custom_prompt_uses_builtin_template",
    "pytests/prompt_test/test_prompt_i18n.py::test_missing_prompt_message_includes_name_and_locale",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-retired-life",
        action="store_true",
        help="Also require the private pinned Life integration locally; never used by public CI",
    )
    args = parser.parse_args()
    manifest = json.loads((ROOT / "neko-patches.json").read_text(encoding="utf-8"))
    for package, expected in manifest["runtime_packages"].items():
        if version(package) != expected:
            raise RuntimeError("Unexpected runtime package: " + package)
    with TemporaryDirectory(prefix="maibot-neko-tests-") as temporary:
        env = dict(
            os.environ,
            MAIBOT_INSTANCE_DIR=temporary,
            MAIBOT_LOCALE="zh-CN",
            PYTHONPATH=str(ROOT / "scripts") + os.pathsep + str(ROOT),
            PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
            PYTHONIOENCODING="utf-8",
            MAIBOT_RUNTIME_CONTEXT=json.dumps(
                {
                    "bot_id": "0" * 32,
                    "http_endpoint": str(Path(temporary) / "synthetic.sock"),
                    "public_base_url": "http://synthetic.invalid",
                    "status_path": str(Path(temporary) / "worker-status.json"),
                }
            ),
            NEKO_RETIRED_LIFE_TESTS="1" if args.include_retired_life else "0",
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "neko_pytest_guard",
                "-p",
                "pytest_asyncio.plugin",
                "-o",
                "asyncio_mode=auto",
                *TESTS,
                *(["pytests/neko_retired/test_life_bridge.py"] if args.include_retired_life else []),
                "-q",
            ],
            cwd=ROOT,
            env=env,
        )
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
