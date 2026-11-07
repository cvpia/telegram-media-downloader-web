from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.bot_service import BotService, normalize_chat_ref
from app.database import Database


def _message(message_id: int, text: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        date=None,
        caption=text,
        text=text,
        audio=None,
        document=None,
        photo=None,
        video=None,
        voice=None,
        video_note=None,
        animation=None,
    )


def _service(tmp_path: Path, client=None) -> tuple[BotService, Database]:
    database = Database(tmp_path / "app.db")
    database.initialize()
    telegram = SimpleNamespace(get_client=lambda: _async_value(client))
    settings = SimpleNamespace(telegram_connected=True)
    service = BotService(
        database,
        settings,
        telegram,
        SimpleNamespace(),
        enabled=False,
    )
    return service, database


async def _async_value(value):
    return value


def test_normalize_chat_ref_supports_ids_usernames_and_links():
    assert normalize_chat_ref("-1001234567890") == "-1001234567890"
    assert normalize_chat_ref("@demo_channel") == "@demo_channel"
    assert normalize_chat_ref("https://t.me/demo_channel/10") == "@demo_channel"
    assert normalize_chat_ref("https://t.me/c/123456789/42") == "-100123456789"


def test_normalize_chat_ref_rejects_private_invite_links():
    with pytest.raises(ValueError):
        normalize_chat_ref("https://t.me/+private_invite")


@pytest.mark.asyncio
async def test_create_rule_resolves_chats_and_sets_latest_checkpoint(tmp_path: Path):
    class FakeClient:
        async def get_chat(self, ref):
            return {
                "@source": SimpleNamespace(id=-100111, title="Source"),
                "@target": SimpleNamespace(id=-100222, title="Target"),
            }[ref]

        async def get_chat_history(self, chat_id, limit):
            assert limit == 1
            yield _message(99)

    service, database = _service(tmp_path, FakeClient())

    rule = await service.create_rule(
        source_chat="@source",
        target_chat="@target",
        filter_expression="media_type == 'video'",
    )

    assert rule["source_chat"] == "-100111"
    assert rule["target_chat"] == "-100222"
    assert rule["last_message_id"] == 99
    assert database.get_forward_rule(rule["id"])["source_title"] == "Source"


@pytest.mark.asyncio
async def test_check_rule_forwards_new_matching_messages_in_order(tmp_path: Path):
    copied = []

    class FakeClient:
        async def get_chat_history(self, chat_id, **kwargs):
            if kwargs.get("offset_id"):
                return
            for message in (_message(13, "three"), _message(12, "two"), _message(11, "one")):
                yield message

        async def copy_message(self, chat_id, from_chat_id, message_id):
            copied.append((chat_id, from_chat_id, message_id))

    service, database = _service(tmp_path, FakeClient())
    rule = database.create_forward_rule(
        source_chat="-100111",
        source_title="Source",
        target_chat="-100222",
        target_title="Target",
        filter_expression="message_id >= 12",
        enabled=True,
        last_message_id=10,
    )

    result = await service.check_rule(rule["id"])

    assert copied == [(-100222, -100111, 12), (-100222, -100111, 13)]
    assert result == {
        "rule_id": rule["id"],
        "forwarded": 2,
        "skipped": 1,
        "checked": 3,
    }
    updated = database.get_forward_rule(rule["id"])
    assert updated["last_message_id"] == 13
    assert updated["forwarded_count"] == 2
    assert updated["last_error"] is None


@pytest.mark.asyncio
async def test_check_rule_keeps_failed_message_as_next_checkpoint(tmp_path: Path):
    class FakeClient:
        async def get_chat_history(self, chat_id, **kwargs):
            if not kwargs.get("offset_id"):
                yield _message(11, "one")

        async def copy_message(self, chat_id, from_chat_id, message_id):
            raise RuntimeError("flood wait")

    service, database = _service(tmp_path, FakeClient())
    rule = database.create_forward_rule(
        source_chat="-100111",
        source_title="Source",
        target_chat="-100222",
        target_title="Target",
        filter_expression="",
        enabled=True,
        last_message_id=10,
    )

    with pytest.raises(RuntimeError, match="flood wait"):
        await service.check_rule(rule["id"])

    updated = database.get_forward_rule(rule["id"])
    assert updated["last_message_id"] == 10
    assert updated["last_error"] == "flood wait"


@pytest.mark.asyncio
async def test_bot_service_starts_handlers_and_reports_status(tmp_path: Path):
    database = Database(tmp_path / "app.db")
    database.initialize()

    class FakeUserClient:
        async def get_me(self):
            return SimpleNamespace(id=77)

    class FakeBot:
        def __init__(self):
            self.is_connected = False
            self.handlers = []
            self.commands = []

        def add_handler(self, handler):
            self.handlers.append(handler)

        async def start(self):
            self.is_connected = True

        async def stop(self):
            self.is_connected = False

        async def get_me(self):
            return SimpleNamespace(id=9001, username="demo_bot")

        async def set_bot_commands(self, commands):
            self.commands = commands

    bot_client = FakeBot()
    telegram = SimpleNamespace(
        get_client=lambda: _async_value(FakeUserClient()),
        create_bot_client=lambda token: bot_client,
    )
    settings = SimpleNamespace(
        get_bot_token=lambda: "123:token",
        telegram_connected=True,
    )
    service = BotService(
        database,
        settings,
        telegram,
        SimpleNamespace(),
        enabled=True,
        monitor_interval=60,
    )

    status = await service.start()

    assert status["running"] is True
    assert status["username"] == "demo_bot"
    assert len(bot_client.handlers) == 2
    assert [command.command for command in bot_client.commands] == [
        "start",
        "help",
        "download",
        "forward",
        "listen_forward",
        "stop_listen",
    ]
    await service.stop()


@pytest.mark.asyncio
async def test_bot_help_only_responds_to_owner(tmp_path: Path):
    service, _ = _service(tmp_path)
    service._owner_id = 77
    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text):
            sent.append((chat_id, text))

    bot = FakeBot()
    await service._handle_command(
        bot,
        SimpleNamespace(
            chat=SimpleNamespace(id=10),
            from_user=SimpleNamespace(id=88),
            text="/start",
        ),
    )
    assert sent == []

    await service._handle_command(
        bot,
        SimpleNamespace(
            chat=SimpleNamespace(id=10),
            from_user=SimpleNamespace(id=77),
            text="/start",
        ),
    )
    assert sent and "/listen_forward" in sent[0][1]
