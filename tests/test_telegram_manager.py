from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
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
        "has_media": True,
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


@pytest.mark.asyncio
async def test_list_dialog_messages_marks_media_preview(tmp_path, monkeypatch):
    manager = TelegramManager(settings=None, session_dir=tmp_path)
    message = SimpleNamespace(
        id=22,
        date=None,
        from_user=None,
        sender_chat=SimpleNamespace(title="Channel"),
        text="",
        caption="Video",
        media=SimpleNamespace(value="video"),
        video=SimpleNamespace(
            file_name="clip.mp4",
            thumbs=[
                SimpleNamespace(file_id="small-thumb", width=90, height=60, file_size=100),
                SimpleNamespace(file_id="large-thumb", width=320, height=180, file_size=400),
            ],
        ),
    )

    class FakeClient:
        async def get_chat_history(self, dialog_id, limit):
            yield message

    async def fake_get_client():
        return FakeClient()

    monkeypatch.setattr(manager, "get_client", fake_get_client)
    result = await manager.list_dialog_messages("-100123", 10)

    assert result[0]["has_media"] is True
    assert result[0]["preview_url"] == (
        "/api/telegram/dialogs/-100123/messages/22/thumbnail"
    )


@pytest.mark.asyncio
async def test_list_dialog_messages_reuses_recent_page_cache(tmp_path, monkeypatch):
    manager = TelegramManager(settings=None, session_dir=tmp_path)
    message = SimpleNamespace(
        id=22,
        date=None,
        from_user=None,
        sender_chat=SimpleNamespace(title="Channel"),
        text="Cached",
        caption="",
        media=None,
    )
    request_count = 0

    class FakeClient:
        async def get_chat_history(self, dialog_id, limit):
            nonlocal request_count
            request_count += 1
            yield message

    async def fake_get_client():
        return FakeClient()

    monkeypatch.setattr(manager, "get_client", fake_get_client)
    first = await manager.list_dialog_messages("-100123", 30)
    second = await manager.list_dialog_messages("-100123", 30)

    assert request_count == 1
    assert second == first
    assert second[0] is not first[0]


@pytest.mark.asyncio
async def test_list_dialog_messages_uses_cached_input_peer(tmp_path, monkeypatch):
    manager = TelegramManager(settings=None, session_dir=tmp_path)
    peer = raw.types.InputPeerChannel(channel_id=123, access_hash=456)
    manager._dialog_peers["-100123"] = peer
    message = SimpleNamespace(
        id=22,
        date=None,
        from_user=None,
        sender_chat=SimpleNamespace(title="Channel"),
        text="Fast",
        caption="",
        media=None,
    )
    invoked = []

    class FakeClient:
        async def invoke(self, request, sleep_threshold):
            invoked.append((request, sleep_threshold))
            return SimpleNamespace()

        def get_chat_history(self, *args, **kwargs):
            raise AssertionError("cached dialogs should not resolve the peer again")

    async def fake_get_client():
        return FakeClient()

    async def fake_parse_messages(client, response, replies):
        assert replies == 0
        return [message]

    monkeypatch.setattr(manager, "get_client", fake_get_client)
    monkeypatch.setattr("app.telegram_manager.utils.parse_messages", fake_parse_messages)
    result = await manager.list_dialog_messages("-100123", 30)

    assert result[0]["text"] == "Fast"
    assert isinstance(invoked[0][0], raw.functions.messages.GetHistory)
    assert invoked[0][0].peer == peer
    assert invoked[0][0].limit == 30
    assert invoked[0][1] == 30


@pytest.mark.asyncio
async def test_get_message_thumbnail_file_uses_largest_thumbnail(tmp_path, monkeypatch):
    manager = TelegramManager(settings=None, session_dir=tmp_path)
    message = SimpleNamespace(
        id=22,
        date=None,
        from_user=None,
        sender_chat=SimpleNamespace(title="Channel"),
        text="",
        caption="Video",
        media=SimpleNamespace(value="video"),
        video=SimpleNamespace(
            file_name="clip.mp4",
            thumbs=[
                SimpleNamespace(file_id="small-thumb", width=90, height=60, file_size=100),
                SimpleNamespace(file_id="large-thumb", width=320, height=180, file_size=400),
            ],
        ),
    )
    manager._message_objects[("-100123", 22)] = message

    class FakeClient:
        async def download_media(self, file_id, file_name, block):
            assert file_id == "large-thumb"
            assert block is True
            Path(file_name).write_bytes(b"thumbnail")
            return file_name

    async def fake_get_client():
        return FakeClient()

    monkeypatch.setattr(manager, "get_client", fake_get_client)
    path, media_type = await manager.get_message_thumbnail_file("-100123", 22)

    assert path.read_bytes() == b"thumbnail"
    assert path.suffix == ".jpg"
    assert media_type == "image/jpeg"


@pytest.mark.asyncio
async def test_get_message_media_file_caches_video(tmp_path, monkeypatch):
    manager = TelegramManager(settings=None, session_dir=tmp_path)
    media = SimpleNamespace(file_name="clip.mp4", mime_type="video/mp4")
    message = SimpleNamespace(
        id=31,
        photo=None,
        video=media,
        audio=None,
        document=None,
        voice=None,
        video_note=None,
        animation=None,
    )

    class FakeClient:
        async def get_messages(self, dialog_id, message_ids):
            assert dialog_id == "-100123"
            assert message_ids == 31
            return message

        async def download_media(self, message_arg, file_name, block):
            assert message_arg is message
            assert block is True
            Path(file_name).write_bytes(b"video-data")
            return file_name

    async def fake_get_client():
        return FakeClient()

    monkeypatch.setattr(manager, "get_client", fake_get_client)
    path, media_type = await manager.get_message_media_file("-100123", 31)

    assert path.read_bytes() == b"video-data"
    assert path.suffix == ".mp4"
    assert media_type == "video/mp4"


@pytest.mark.asyncio
async def test_list_dialog_messages_uses_before_id_for_older_page(tmp_path, monkeypatch):
    manager = TelegramManager(settings=None, session_dir=tmp_path)
    older = SimpleNamespace(
        id=10,
        date=None,
        from_user=None,
        sender_chat=SimpleNamespace(title="Channel"),
        text="Older",
        caption="",
        media=None,
    )

    class FakeClient:
        async def get_chat_history(self, dialog_id, limit, offset_id):
            assert dialog_id == "-100123"
            assert limit == 50
            assert offset_id == 60
            yield older

    async def fake_get_client():
        return FakeClient()

    monkeypatch.setattr(manager, "get_client", fake_get_client)
    messages = await manager.list_dialog_messages("-100123", limit=50, before_id=60)

    assert [message["id"] for message in messages] == [10]
