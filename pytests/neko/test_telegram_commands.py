"""Telegram command addressing uses the adapter's getMe identity, without network IO."""

from copy import deepcopy

import pytest
import neko_pytest_guard  # noqa: F401 -- deny network in standalone synthetic runs

from src.plugin_runtime.host.message_utils import PluginMessageUtils
from src.plugins.built_in.telegram_adapter.codec import decode_message, utf16_length


def _payload(text, entities, *, caption=False, chat_type="private"):
    return {
        "message_id": 42,
        "date": 1791504000,
        "chat": {"id": 123 if chat_type == "private" else -123, "type": chat_type, "title": "Synthetic group"},
        "from": {"id": 123, "first_name": "Synthetic user"},
        "caption" if caption else "text": text,
        "caption_entities" if caption else "entities": entities,
    }


@pytest.mark.parametrize("caption", [False, True])
@pytest.mark.parametrize(
    "command,username,expected,is_command",
    [
        ("/draw@Test_Bot", "Test_Bot", "/draw", True),
        ("/draw@tEsT_bOt", "TEST_BOT", "/draw", True),
        ("/draw@Other_Bot", "Test_Bot", "/draw@Other_Bot", False),
        ("/draw@Test_Bot_extra", "Test_Bot", "/draw@Test_Bot_extra", False),
        ("/draw@Test_Bot", "", "/draw@Test_Bot", False),
        ("/draw@", "Test_Bot", "/draw@", False),
        ("/draw@Teſt_Bot", "Test_Bot", "/draw@Teſt_Bot", False),
        ("/draw", "Test_Bot", "/draw", True),
        ("/help@Test_Bot", "Test_Bot", "/help", True),
        ("/HELP@Test_Bot", "Test_Bot", "/HELP", True),
        ("/help", "Test_Bot", "/help", True),
    ],
)
def test_command_addressing_preserves_arguments(command, username, expected, is_command, caption):
    arguments = "  猫😀\n/draw@Other_Bot user@example.com  "
    payload = _payload(
        command + arguments, [{"type": "bot_command", "offset": 0, "length": utf16_length(command)}], caption=caption
    )
    original = deepcopy(payload)

    result = decode_message(payload, "999", username, [])

    assert result["raw_message"] == [{"type": "text", "data": expected + arguments}]
    assert result["is_command"] is is_command
    assert result["is_at"] is result["is_mentioned"] is False
    assert payload == original
    # Host accepts the same normalized body and command flag, without any plugin-specific rewrite.
    restored = PluginMessageUtils._session_message_to_dict(PluginMessageUtils._build_session_message_from_dict(result))
    assert restored["raw_message"] == result["raw_message"]
    assert restored["is_command"] is is_command
    assert restored["account_id"] == "999"


@pytest.mark.parametrize(
    "chat_type,topic", [("private", False), ("group", False), ("supergroup", False), ("supergroup", True)]
)
@pytest.mark.parametrize("caption", [False, True])
def test_command_keeps_utf16_mentions_media_reply_and_chat_identity(chat_type, topic, caption):
    command = "/draw@Test_Bot"
    prefix = command + " 😀猫 "
    text = prefix + "@Other_Bot 与 @Test_Bot"
    entities = [
        {"type": "bot_command", "offset": 0, "length": utf16_length(command)},
        {"type": "mention", "offset": utf16_length(prefix), "length": len("@Other_Bot")},
        {"type": "mention", "offset": utf16_length(prefix + "@Other_Bot 与 "), "length": len("@Test_Bot")},
    ]
    payload = _payload(text, entities, caption=caption, chat_type=chat_type)
    payload["reply_to_message"] = {
        "message_id": 41,
        "text": "/draw@Test_Bot original",
        "from": {"id": 456, "first_name": "Author"},
    }
    if topic:
        payload.update(is_topic_message=True, message_thread_id=7)
        payload["chat"]["is_forum"] = True
    original = deepcopy(payload)
    media = [{"type": "image", "data": "synthetic image"}]

    result = decode_message(payload, "999", "Test_Bot", media, "Art")

    assert result["raw_message"][0]["data"]["target_message_content"] == "/draw@Test_Bot original"
    assert result["raw_message"][1:] == [
        {"type": "text", "data": "/draw 😀猫 "},
        {"type": "at", "data": {"target_user_id": "@Other_Bot", "target_user_nickname": "@Other_Bot"}},
        {"type": "text", "data": " 与 "},
        {"type": "at", "data": {"target_user_id": "999", "target_user_nickname": "@Test_Bot"}},
        *media,
    ]
    assert result["is_command"] and result["is_picture"] and result["is_mentioned"]
    assert result["message_id"] == ("123:42" if chat_type == "private" else "-123:42")
    info = result["message_info"]
    assert info["user_info"]["user_id"] == "123"
    if chat_type == "private":
        assert "group_info" not in info
    else:
        assert info["group_info"] == {
            "group_id": "-123:7" if topic else "-123",
            "group_name": "Synthetic group / Art" if topic else "Synthetic group",
        }
    assert ("telegram_message_thread_id" in info["additional_config"]) is topic
    assert payload == original


@pytest.mark.parametrize(
    "text,entities",
    [
        ("/draw@Test_Bot", []),
        ("普通正文 /draw@Test_Bot", []),
        ("😀 /draw@Test_Bot", [{"type": "bot_command", "offset": 3, "length": len("/draw@Test_Bot")}]),
        ("/draw@Test_Bot", [{"type": "code", "offset": 0, "length": len("/draw@Test_Bot")}]),
    ],
)
def test_non_command_text_is_not_rewritten(text, entities):
    result = decode_message(_payload(text, entities), "999", "Test_Bot", [])
    assert result["raw_message"] == [{"type": "text", "data": text}]
    assert result["is_command"] is False


def test_command_only_and_wrong_bot_with_actual_mention():
    result = decode_message(
        _payload("/draw@Test_Bot", [{"type": "bot_command", "offset": 0, "length": 14}]), "999", "Test_Bot", []
    )
    assert result["raw_message"] == [{"type": "text", "data": "/draw"}]
    text = "/draw@Other_Bot @Test_Bot"
    entities = [
        {"type": "bot_command", "offset": 0, "length": len("/draw@Other_Bot")},
        {"type": "mention", "offset": len("/draw@Other_Bot "), "length": len("@Test_Bot")},
    ]
    result = decode_message(_payload(text, entities), "999", "Test_Bot", [])
    assert result["raw_message"][0] == {"type": "text", "data": "/draw@Other_Bot "}
    assert result["is_command"] is False
    assert result["is_mentioned"] is True
