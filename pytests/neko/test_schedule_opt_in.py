"""An upstream upgrade must not activate a second scheduler or touch retained plans."""

from unittest.mock import AsyncMock

import asyncio
import pytest

from src.config.config import global_config
from src.config.official_configs import CapabilitiesConfig, ExperimentalConfig
from src.maisaka.builtin_tool import get_all_builtin_tool_specs
from src.services.schedules.service import ScheduleService


def test_existing_configuration_does_not_opt_into_new_actions(monkeypatch):
    assert not CapabilitiesConfig.model_validate({"files": False}).chat_schedules
    experimental = ExperimentalConfig.model_validate({"focus_mode": True})
    assert experimental.lightweight_private_chats == []
    assert not experimental.tool_search_tail_loading
    monkeypatch.setattr(global_config.capabilities, "chat_schedules", False)
    assert "schedule" not in {spec.name for spec in get_all_builtin_tool_specs()}
    monkeypatch.setattr(global_config.capabilities, "chat_schedules", True)
    assert "schedule" in {spec.name for spec in get_all_builtin_tool_specs()}


@pytest.mark.asyncio
async def test_disabled_scheduler_preserves_file_and_cannot_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(global_config.capabilities, "chat_schedules", False)
    path = tmp_path / "todos.json"
    # Even unrecognised retained data must not be loaded, repaired or replayed.
    retained = b'{"retained": "synthetic old schedule"}'
    path.write_bytes(retained)
    service = ScheduleService(path)
    await service.start()
    assert service._store is None and service._worker is None
    dispatch = AsyncMock()
    monkeypatch.setattr(service, "dispatch", dispatch)
    await service.tick()
    dispatch.assert_not_awaited()
    with pytest.raises(PermissionError, match="未启用"):
        await service.command({"action": "add"})
    assert path.read_bytes() == retained
    await service.stop()


@pytest.mark.asyncio
async def test_disabled_dispatch_does_not_read_or_finish_claimed_item(monkeypatch):
    monkeypatch.setattr(global_config.capabilities, "chat_schedules", False)
    service = ScheduleService()

    def forbidden(*_args):
        pytest.fail("disabled dispatch accessed retained schedule state")

    monkeypatch.setattr(service, "_execute", forbidden)
    await service.dispatch({"id": "synthetic-already-claimed", "revision": 1})


@pytest.mark.asyncio
async def test_disable_while_command_waits_does_not_write(monkeypatch):
    monkeypatch.setattr(global_config.capabilities, "chat_schedules", True)
    service = ScheduleService()
    entered = asyncio.Event()
    release = asyncio.Event()

    class WaitingLock:
        async def __aenter__(self):
            entered.set()
            await release.wait()

        async def __aexit__(self, *_args):
            return False

    def forbidden(*_args):
        pytest.fail("revoked queued command wrote schedule state")

    service._lock = WaitingLock()
    monkeypatch.setattr(service, "_execute", forbidden)
    operation = asyncio.create_task(service.command({"action": "add"}))
    await entered.wait()
    monkeypatch.setattr(global_config.capabilities, "chat_schedules", False)
    release.set()
    with pytest.raises(PermissionError, match="未启用"):
        await operation
