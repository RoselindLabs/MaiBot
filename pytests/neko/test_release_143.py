"""Real Focus policy boundaries using synthetic pre-migration configuration."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from neko_prepare_143 import prepare_focus_groups
from src.config.config import global_config
from src.config.official_configs import ChatStreamGroup, TargetItem
from src.maisaka.focus import manager as manager_module


def old_config():
    return {
        "inner": {"version": "8.15.68"},
        "experimental": {
            "focus_mode": True,
            "focus_groups": [],
            "focus_on_private": False,
            "focus_chat_whitelist": [{"platform": "qq", "item_id": "allowed", "rule_type": "group"}],
        },
        "personality": {"personality": "synthetic unchanged"},
    }


def test_old_global_copy_is_explicit_idempotent_and_preserves_other_values():
    original = old_config()
    before = deepcopy(original)
    prepared = prepare_focus_groups(original)
    assert original == before
    assert prepared["experimental"]["focus_groups"] == [
        {
            "targets": [
                {"platform": "*", "item_id": "*", "rule_type": "group"},
            ]
        }
    ]
    expected = deepcopy(before)
    expected["experimental"]["focus_groups"] = prepared["experimental"]["focus_groups"]
    assert prepared == expected
    assert prepare_focus_groups(prepared) == prepared


@pytest.mark.parametrize("case", ["disabled", "scoped", "new"])
def test_disabled_explicit_groups_and_new_instance_unchanged(case):
    data = old_config()
    if case == "disabled":
        data["experimental"]["focus_mode"] = False
    elif case == "scoped":
        data["experimental"]["focus_groups"] = [{"targets": []}]
    else:
        data["inner"]["version"] = "8.16.18"
    assert prepare_focus_groups(data) == data


@pytest.mark.parametrize("key", ["focus_mode", "focus_groups", "focus_on_private"])
def test_ambiguous_old_settings_fail_without_mutating(key):
    data = old_config()
    del data["experimental"][key]
    before = deepcopy(data)
    with pytest.raises(ValueError):
        prepare_focus_groups(data)
    assert data == before


def test_migrated_group_uses_real_manager_whitelist_private_and_disabled_boundaries(monkeypatch):
    prepared = prepare_focus_groups(old_config())["experimental"]
    monkeypatch.setattr(global_config.experimental, "focus_mode", True)
    monkeypatch.setattr(global_config.experimental, "focus_on_private", False)
    monkeypatch.setattr(
        global_config.experimental,
        "focus_groups",
        [ChatStreamGroup.model_validate(group) for group in prepared["focus_groups"]],
    )
    monkeypatch.setattr(
        global_config.experimental,
        "focus_chat_whitelist",
        [TargetItem.model_validate(target) for target in prepared["focus_chat_whitelist"]],
    )
    sessions = {
        sid: SimpleNamespace(
            session_id=sid,
            platform="qq",
            is_group_session=group,
            group_id=sid if group else None,
            user_id=None if group else sid,
        )
        for sid, group in (("allowed", True), ("denied", True), ("private", False))
    }
    monkeypatch.setattr(manager_module.chat_manager, "get_session_by_session_id", sessions.get)
    manager = manager_module.FocusModeManager()
    assert manager.is_enabled_for_session("allowed")
    assert not manager.is_enabled_for_session("denied")
    assert not manager.is_enabled_for_session("private")
    assert not manager.is_enabled_for_session("missing")
    monkeypatch.setattr(global_config.experimental, "focus_mode", False)
    assert not manager.is_enabled_for_session("allowed")
    monkeypatch.setattr(global_config.experimental, "focus_mode", True)
    monkeypatch.setattr(global_config.experimental, "focus_groups", [])
    assert not manager.is_enabled_for_session("allowed")
