"""Private peer identity through the real receive chain, using synthetic services."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.chat.message_receive.chat_manager import BotChatSession, ChatManager
from src.common.utils.utils_session import SessionUtils
from src.platform_io.manager import PlatformIOManager
from src.platform_io.types import DriverKind, InboundMessageEnvelope, RouteKey
from src.plugin_runtime.host.message_utils import PluginMessageUtils
from src.plugin_runtime.host.supervisor import PluginRunnerSupervisor
from src.maisaka.context.history import has_self_message
from src.maisaka.context.messages import SessionBackedMessage
from src.maisaka.reasoning_engine import MaisakaReasoningEngine
from src.maisaka.runtime import MaisakaHeartFlowChatting


def wire(*, sender="bot", peer="peer", account="bot", scope="one", group=None):
    info = {
        "user_info": {"user_id": sender, "user_nickname": "Bot" if sender == account else "Peer"},
        "additional_config": {"platform_io_target_user_id": peer},
    }
    if group is not None:
        info["group_info"] = {"group_id": group, "group_name": "Group"}
    return {
        "message_id": "-123",
        "timestamp": "1791504000.0",
        "platform": "qq",
        "account_id": account,
        "scope": scope,
        "message_info": info,
        "raw_message": [{"type": "text", "data": "same text"}],
    }


def internal(**kwargs):
    return PluginMessageUtils._build_session_message_from_dict(wire(**kwargs))


def context_message(**kwargs):
    message = internal(**kwargs)
    ChatManager().register_message(message)
    return message


def history_item(message):
    return SessionBackedMessage.from_session_message(
        message,
        raw_message=message.raw_message,
        visible_text="same text",
        source_kind="guided_reply",
    )


@pytest.mark.parametrize(
    "sender,peer,expected", [("bot", "peer", "peer"), ("peer", "bot", "peer"), ("bot", "bot", "bot")]
)
def test_policy_and_registration_agree_on_private_peer(sender, peer, expected):
    data = wire(sender=sender, peer=peer)
    assert PluginRunnerSupervisor._resolve_policy_target(data) == ("private", expected)
    message = PluginMessageUtils._build_session_message_from_dict(data)
    manager = ChatManager()
    manager.register_message(message)
    expected_id = SessionUtils.calculate_session_id("qq", user_id=expected, account_id="bot", scope="one")
    assert message.session_id == expected_id
    assert message.message_info.user_info.user_id == sender
    assert list(manager.last_messages) == [expected_id]


@pytest.mark.parametrize("target", [None, "", "  ", 42, True, []])
def test_unknown_self_peer_cannot_create_self_session(target):
    message = internal(peer=target)
    manager = ChatManager()
    with pytest.raises(ValueError, match="接收者"):
        manager.register_message(message)
    with pytest.raises(ValueError, match="接收者"):
        PluginRunnerSupervisor._resolve_policy_target(wire(peer=target))
    assert not manager.last_messages


def test_self_echo_does_not_replace_peer_name_or_card():
    manager = ChatManager()
    session = BotChatSession("session", "qq", user_id="peer", user_nickname="Peer", user_cardname="Peer card")
    assert not manager._update_session_identity(session, internal())
    assert (session.user_nickname, session.user_cardname) == ("Peer", "Peer card")
    message = internal(sender="peer")
    message.message_info.user_info.user_nickname = "New peer name"
    assert manager._update_session_identity(session, message)
    assert session.user_nickname == "New peer name"


def test_auxiliary_metadata_cannot_change_policy_approved_peer():
    message = internal()
    PluginRunnerSupervisor._attach_inbound_route_metadata(
        message,
        RouteKey("qq", "bot", "one"),
        {"platform_io_target_user_id": "other", "platform_io_target_group_id": "group", "custom": "kept"},
    )
    assert message.message_info.additional_config == {"platform_io_target_user_id": "peer", "custom": "kept"}


def dedupe_key(**fields):
    message = internal(**fields)
    return PlatformIOManager._build_inbound_dedupe_key(
        InboundMessageEnvelope(
            route_key=RouteKey(message.platform, message.account_id, message.scope),
            driver_id="same-driver",
            driver_kind=DriverKind.PLUGIN,
            external_message_id=message.message_id,
            dedupe_key=message.message_id,
            session_message=message,
        )
    )


def test_same_platform_id_in_different_routes_or_conversations_is_not_dropped():
    keys = [
        dedupe_key(),
        dedupe_key(peer="other"),
        dedupe_key(scope="two"),
        dedupe_key(account="bot2", sender="bot2"),
        dedupe_key(sender="peer"),
        dedupe_key(group="group1"),
        dedupe_key(group="group2"),
    ]
    assert len(set(keys)) == len(keys)
    assert dedupe_key() == keys[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sender,group,expected_peer", [("bot", None, "peer"), ("peer", None, "peer"), ("bot", "group", "bot")]
)
async def test_real_receive_chain_keeps_peer_before_hooks_and_after_registration(
    monkeypatch, sender, group, expected_peer
):
    from src.chat.message_receive import bot
    from src.files import service as files

    manager = ChatManager()
    manager._save_session = Mock()
    peer_id = SessionUtils.calculate_session_id(
        "qq", user_id=expected_peer, group_id=group, account_id="bot", scope="one"
    )
    manager.sessions[peer_id] = BotChatSession(
        peer_id,
        "qq",
        user_id=None if group else expected_peer,
        user_nickname="Peer",
        group_id=group,
        account_id="bot",
        scope="one",
    )
    monkeypatch.setattr(bot, "chat_manager", manager)
    monkeypatch.setattr(
        bot, "process_received_images_in_message", lambda _: SimpleNamespace(compressed_count=0, discarded_count=0)
    )
    monkeypatch.setattr(bot.MessageUtils, "check_ban_words", lambda _: (False, None))
    monkeypatch.setattr(bot.MessageUtils, "check_ban_regex", lambda _: (False, None))
    file_ingest = AsyncMock()
    monkeypatch.setattr(files, "get_file_service", lambda: SimpleNamespace(ingest=file_ingest))
    receiver = bot.ChatBot()
    observed = []

    async def hook(_name, message):
        observed.append(message.session_id)
        return SimpleNamespace(aborted=False), message

    receiver._invoke_message_hook = hook
    receiver.handle_notice_message = AsyncMock()
    receiver._process_adapter_lifecycle_command = AsyncMock(return_value=False)
    receiver._process_clear_context_command = AsyncMock(return_value=False)
    receiver._process_commands = AsyncMock(return_value=(False, None, True))
    receiver.heartflow_message_receiver = SimpleNamespace(process_message=AsyncMock())
    message = internal(sender=sender, group=group)
    message.process = AsyncMock()
    message.processed_plain_text = "same text"
    await receiver.receive_message(message)
    receiver.heartflow_message_receiver.process_message.assert_awaited_once_with(message)
    assert observed == [peer_id, peer_id]
    assert message.session_id == peer_id
    assert message.message_info.user_info.user_id == sender
    assert list(manager.sessions) == [peer_id]
    if not group:
        assert manager.sessions[peer_id].user_nickname == "Peer"


@pytest.mark.asyncio
async def test_file_source_uses_conversation_peer_not_self_author(monkeypatch):
    from src.plugin_runtime.integration import PluginRuntimeManager
    from src.chat.message_receive.bot import chat_bot
    import src.platform_io

    descriptor = SimpleNamespace(plugin_id="adapter", account_id="bot", scope="one", platform="qq")
    monkeypatch.setattr(
        src.platform_io,
        "get_platform_io_manager",
        lambda: SimpleNamespace(
            driver_registry=SimpleNamespace(get=lambda _: SimpleNamespace(descriptor=descriptor)),
        ),
    )
    receive = AsyncMock()
    monkeypatch.setattr(chat_bot, "receive_message", receive)
    message = internal()
    envelope = InboundMessageEnvelope(
        route_key=RouteKey("qq", "bot", "one"),
        driver_id="adapter:gateway",
        driver_kind=DriverKind.PLUGIN,
        session_message=message,
    )
    await PluginRuntimeManager._dispatch_platform_inbound(None, envelope)
    receive.assert_awaited_once_with(message)
    assert message.file_source["user_id"] == "peer"
    assert message.message_info.user_info.user_id == "bot"


def test_context_identity_never_merges_other_peers_accounts_or_message_ids():
    message = context_message()
    history = [history_item(message)]
    assert has_self_message(history, context_message())
    for other in [
        context_message(peer="other"),
        context_message(scope="other"),
        context_message(account="bot2", sender="bot2"),
        context_message(sender="peer"),
    ]:
        assert not has_self_message(history, other)
    other = context_message()
    other.message_id = "-124"
    assert not has_self_message(history, other)


@pytest.mark.asyncio
async def test_echo_after_send_does_not_enqueue_or_trigger_another_turn():
    runtime = object.__new__(MaisakaHeartFlowChatting)
    runtime._chat_history = [history_item(context_message())]
    # Returning before any runtime scheduler/monitor/media access is intentional.
    await runtime._register_message(context_message())
    assert len(runtime._chat_history) == 1


def test_send_after_echo_keeps_one_context_but_reports_successful_send():
    runtime = object.__new__(MaisakaHeartFlowChatting)
    runtime._chat_history = [history_item(context_message())]
    runtime._emit_monitor_message_sent = Mock()
    assert runtime.append_sent_message_to_chat_history(context_message())
    assert len(runtime._chat_history) == 1
    runtime._emit_monitor_message_sent.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("send_during_build", [False, True])
async def test_queued_echo_is_deduplicated_even_if_send_completes_during_media_build(send_during_build):
    message = context_message()
    runtime = SimpleNamespace(_chat_history=[] if send_during_build else [history_item(message)])
    engine = object.__new__(MaisakaReasoningEngine)
    engine._runtime = runtime

    async def build(incoming):
        runtime._chat_history.append(history_item(message))
        return history_item(incoming)

    engine._build_history_message = AsyncMock(side_effect=build)
    await engine._ingest_messages([message])
    assert len(runtime._chat_history) == 1
    assert engine._build_history_message.await_count == int(send_during_build)


@pytest.mark.asyncio
async def test_manual_self_message_is_kept_and_marked_as_self_in_context():
    engine = object.__new__(MaisakaReasoningEngine)
    engine._runtime = SimpleNamespace(_chat_history=[], _is_focus_mode_active_for_current_chat=lambda: False)
    message = context_message()
    message.processed_plain_text = "same text"
    engine._build_message_sequence = AsyncMock(return_value=message.raw_message)
    await engine._ingest_messages([message])
    assert len(engine._runtime._chat_history) == 1
    assert engine._runtime._chat_history[0].source_kind == "guided_reply"
    assert engine._runtime._chat_history[0].original_message.message_info.user_info.user_id == "bot"


@pytest.mark.asyncio
async def test_restart_restore_projects_one_self_message_without_changing_database_rows(monkeypatch):
    import src.maisaka.runtime as runtime_module

    first, echo = context_message(), context_message()
    later = context_message()
    later.message_id = "-124"  # Same text, distinct platform message: must survive.
    rows = [first, echo, later]
    monkeypatch.setattr(runtime_module, "find_messages", lambda **_: rows)
    runtime = object.__new__(MaisakaHeartFlowChatting)
    runtime.session_id = first.session_id
    runtime._chat_history = []
    runtime.message_cache = []
    runtime._get_context_restore_limit = lambda: 50
    runtime._quote_counter = SimpleNamespace(record=Mock())
    runtime._resolve_restored_message_source_kind = lambda _: "guided_reply"
    runtime._reasoning_engine = SimpleNamespace(
        _build_history_message=AsyncMock(side_effect=lambda message, **_: history_item(message))
    )
    runtime._build_context_restore_reference_message = lambda *_, **__: None
    runtime.log_prefix = "synthetic"
    await runtime._restore_recent_context_from_db()
    assert [item.message_id for item in runtime._chat_history] == ["-123", "-124"]
    assert rows == [first, echo, later]
    assert runtime._quote_counter.record.call_count == 2
