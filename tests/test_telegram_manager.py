from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest
from pyrogram.errors import BadMsgNotification
from pyrogram import raw

from app.telegram_manager import TelegramManager


class FakeClient:
    def __init__(self, failures: int = 0):
        self.failures = failures
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.send_code_calls = 0

    async def connect(self):
        self.connect_calls += 1
        return True

    async def disconnect(self):
        self.disconnect_calls += 1

    async def send_code(self, phone_number):
        self.send_code_calls += 1
        if self.send_code_calls <= self.failures:
            raise BadMsgNotification(16)
        return SimpleNamespace(phone_code_hash="phone-hash", type="app")



def test_dialog_info_for_channel_and_user():
    channel = SimpleNamespace(
        id=123,
        title="Private Channel",
        username="private_channel",
        megagroup=False,
        photo=SimpleNamespace(photo_id=987654),
    )
    channel_info = TelegramManager._dialog_info(
        raw.types.PeerChannel(channel_id=123),
        {},
        {123: channel},
    )
    assert channel_info == {
        "id": "-1000000000123",
        "title": "Private Channel",
        "type": "channel",
        "username": "private_channel",
        "has_photo": True,
        "avatar_url": "/api/telegram/dialogs/-1000000000123/avatar?v=987654",
    }

    user = SimpleNamespace(
        id=456,
        first_name="Test",
        last_name="User",
        username="testuser",
        bot=False,
        photo=None,
    )
    user_info = TelegramManager._dialog_info(
        raw.types.PeerUser(user_id=456),
        {456: user},
        {},
    )
    assert user_info["type"] == "private"
    assert user_info["title"] == "Test User"
    assert user_info["has_photo"] is False
    assert user_info["avatar_url"] == ""


def test_clock_sync_codes_are_detected():
    assert TelegramManager.is_clock_sync_error(BadMsgNotification(16))
    assert TelegramManager.is_clock_sync_error(BadMsgNotification(17))
    assert TelegramManager.is_clock_sync_error(BadMsgNotification(20))
    assert not TelegramManager.is_clock_sync_error(BadMsgNotification(18))


@pytest.mark.asyncio
async def test_send_login_code_retries_after_clock_sync_error(tmp_path, monkeypatch):
    manager = TelegramManager(settings=None, session_dir=tmp_path)
    fake_client = FakeClient(failures=1)
    monkeypatch.setattr(manager, "_new_client", lambda *args: fake_client)

    client, sent_code = await manager._send_login_code(
        "login_test", "+8613800000000", 1, "hash"
    )

    assert client is fake_client
    assert sent_code.phone_code_hash == "phone-hash"
    assert fake_client.connect_calls == 2
    assert fake_client.disconnect_calls == 1
    assert fake_client.send_code_calls == 2


@pytest.mark.asyncio
async def test_send_login_code_reports_persistent_clock_error(tmp_path, monkeypatch):
    manager = TelegramManager(settings=None, session_dir=tmp_path)
    fake_client = FakeClient(failures=2)
    monkeypatch.setattr(manager, "_new_client", lambda *args: fake_client)

    with pytest.raises(ValueError, match="时间不同步"):
        await manager._send_login_code("login_test", "+8613800000000", 1, "hash")

    assert fake_client.connect_calls == 2
    assert fake_client.disconnect_calls == 2
    assert fake_client.send_code_calls == 2


def test_message_info_serializes_caption_and_media():
    message = SimpleNamespace(
        id=77,
        date=datetime(2026, 11, 7, 10, 30, tzinfo=timezone.utc),
        from_user=SimpleNamespace(
            id=456,
            first_name="Media",
            last_name="Bot",
            username="media_bot",
        ),
        sender_chat=None,
        text="",
        caption="Video caption",
        media=SimpleNamespace(value="video"),
        video=SimpleNamespace(file_name="demo.mp4"),
    )

    info = TelegramManager._message_info(message)

    assert info == {
        "id": 77,
        "date": "2026-11-07T10:30:00+00:00",
        "sender": "Media Bot",
        "text": "Video caption",
        "media_type": "video",
        "media_label": "视频",
        "media_name": "demo.mp4",
    }


@pytest.mark.asyncio
async def test_list_dialog_messages_returns_chronological_history(tmp_path, monkeypatch):
    manager = TelegramManager(settings=None, session_dir=tmp_path)
    older = SimpleNamespace(
        id=1,
        date=None,
        from_user=None,
        sender_chat=SimpleNamespace(title="Channel"),
        text="Older",
        caption="",
        media=None,
    )
    newer = SimpleNamespace(
        id=2,
        date=None,
        from_user=None,
        sender_chat=SimpleNamespace(title="Channel"),
        text="Newer",
        caption="",
        media=None,
    )

    class FakeMessageClient:
        async def get_chat_history(self, dialog_id, limit):
            assert dialog_id == "-100123"
            assert limit == 20
            yield newer
            yield older

    async def fake_get_client():
        return FakeMessageClient()

    monkeypatch.setattr(manager, "get_client", fake_get_client)
    messages = await manager.list_dialog_messages("-100123", limit=20)

    assert [message["id"] for message in messages] == [1, 2]
    assert [message["text"] for message in messages] == ["Older", "Newer"]


@pytest.mark.asyncio
async def test_get_dialog_avatar_downloads_small_photo(tmp_path, monkeypatch):
    manager = TelegramManager(settings=None, session_dir=tmp_path)
    downloaded = BytesIO(b"avatar-bytes")
    downloaded.name = "avatar.jpg"

    class FakeAvatarClient:
        async def get_chat(self, dialog_id, force_full=False):
            assert dialog_id == "-100123"
            assert force_full is False
            return SimpleNamespace(
                photo=SimpleNamespace(
                    small_file_id="small-photo-file-id",
                    big_file_id="big-photo-file-id",
                )
            )

        async def download_media(self, file_id, in_memory=False):
            assert file_id == "small-photo-file-id"
            assert in_memory is True
            return downloaded

    async def fake_get_client():
        return FakeAvatarClient()

    monkeypatch.setattr(manager, "get_client", fake_get_client)
    avatar = await manager.get_dialog_avatar("-100123")

    assert avatar == (b"avatar-bytes", "image/jpeg")
