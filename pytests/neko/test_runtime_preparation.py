"""Bounded release preparation; synthetic config only, never a live instance."""

from copy import deepcopy

import pytest

from src.config.config_upgrade_hooks import apply_config_upgrade_hooks
from neko_prepare_1318 import split_attention_drift


@pytest.mark.parametrize(
    ("existing_http", "expected_http"),
    [
        (None, {"host": ["127.0.0.2"], "port": 18234}),
        ({"host": ["127.0.0.3"], "port": 18235}, {"host": ["127.0.0.3"], "port": 18235}),
        ({"port": 18236}, {"host": ["127.0.0.2"], "port": 18236}),
        ({"host": ["127.0.0.4"]}, {"host": ["127.0.0.4"], "port": 18234}),
        ({"host": [], "port": 0}, {"host": [], "port": 0}),
    ],
)
def test_http_migration_preserves_explicit_fields_and_unrelated_settings(existing_http, expected_http):
    config = {
        "webui": {"host": "127.0.0.2", "port": 18234, "enabled": True},
        "personality": {"personality_core": "synthetic unchanged persona"},
        "model_task_config": {"replyer": {"model_list": ["synthetic-model"], "max_tokens": 1234}},
        "capabilities": {"files": False},
        "desktop_pet": {"enabled": False, "chat_tools_enabled": False},
    }
    if existing_http is not None:
        config["http"] = existing_http
    original = deepcopy(config)
    migrated = apply_config_upgrade_hooks(config, "bot_config.toml", "8.15.59", "8.15.62")
    assert migrated.migrated
    assert migrated.data["http"] == expected_http
    assert migrated.data["webui"] == {"enabled": True}
    for section in ("personality", "model_task_config", "capabilities", "desktop_pet"):
        assert migrated.data[section] == original[section]
    again = apply_config_upgrade_hooks(migrated.data, "bot_config.toml", "8.15.62", "8.15.62")
    assert not again.migrated
    assert again.data == migrated.data


def test_new_version_does_not_enable_native_files_or_desktop_handoff():
    from src.config.config import global_config
    from src.desktop_pet.config import DesktopPetConfig

    assert global_config.capabilities.files is False
    defaults = DesktopPetConfig()
    assert defaults.enabled is False
    assert defaults.chat_tools_enabled is False


@pytest.mark.parametrize("enabled", [False, True])
def test_1318_copy_migration_preserves_both_scopes_and_other_settings(enabled):
    old = {
        "experimental": {
            "attention_drift": {"enabled": enabled, "level": "high", "anchor_policy": "strict"},
            "focus_mode": True,
            "focus_on_private": False,
        },
        "personality": {"personality": "synthetic unchanged persona"},
        "http": {"port": 18234},
        "capabilities": {"files": False},
    }
    original = deepcopy(old)
    migrated = split_attention_drift(old)
    expected = deepcopy(old)
    legacy = expected["experimental"].pop("attention_drift")
    expected["experimental"].update(group_attention_drift=legacy, private_attention_drift=legacy)
    assert migrated == expected
    assert old == original
    assert split_attention_drift(migrated) == migrated
    migrated["experimental"]["group_attention_drift"]["enabled"] = not enabled
    assert migrated["experimental"]["private_attention_drift"]["enabled"] is enabled


def test_1318_copy_migration_keeps_explicit_scope_values_and_fills_missing_fields():
    old = {
        "experimental": {
            "attention_drift": {"enabled": True, "anchor_policy": "strict"},
            "group_attention_drift": {"enabled": False},
            "private_attention_drift": {"anchor_policy": "loose"},
        }
    }
    result = split_attention_drift(old)["experimental"]
    assert result == {
        "group_attention_drift": {"enabled": False, "anchor_policy": "strict"},
        "private_attention_drift": {"enabled": True, "anchor_policy": "loose"},
    }
    assert split_attention_drift({"experimental": {"focus_mode": True}}) == {"experimental": {"focus_mode": True}}


@pytest.mark.parametrize(
    "experimental", [{"attention_drift": False}, {"attention_drift": {}, "group_attention_drift": 1}]
)
def test_1318_copy_migration_rejects_invalid_tables(experimental):
    with pytest.raises(ValueError, match="must be a table"):
        split_attention_drift({"experimental": experimental})


@pytest.mark.parametrize("locale", ["zh-CN", "en-US", "ja-JP"])
def test_relocated_templates_resolve_with_metadata(locale):
    from src.common.prompt_i18n import list_prompt_templates, load_prompt_template

    names = (
        "agent_acp",
        "agent_acp_environment",
        "agent_acp_failure",
        "agent_planner",
        "agent_planner_continue",
        "agent_planner_resume",
        "a_memorix_fact_deduplication",
    )
    templates = list_prompt_templates(locale=locale)
    for name in names:
        resolved = load_prompt_template(name, locale=locale)
        assert resolved.template
        assert templates[name].path == resolved.path
        assert templates[name].metadata.display_name
        assert templates[name].metadata.description
