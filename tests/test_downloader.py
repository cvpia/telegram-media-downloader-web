from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.downloader import (
    DownloaderService,
    build_destination,
    build_group_destination,
    detect_media,
    format_is_allowed,
    media_extension,
)
from app.schemas import parse_date_bound
from app.telegram_manager import TelegramManager


def test_detect_media_prefers_known_media_type():
    message = SimpleNamespace(document=None, photo=SimpleNamespace(file_unique_id="p1"))
    media_type, media = detect_media(message)
    assert media_type == "photo"
    assert media.file_unique_id == "p1"


def test_format_filter():
    configured = {"video": ["mp4", "mkv"], "document": ["all"]}
    assert format_is_allowed("video", ".mp4", configured)
    assert not format_is_allowed("video", ".avi", configured)
    assert format_is_allowed("document", ".anything", configured)
    assert format_is_allowed("photo", ".jpg", configured)


def test_media_extension_uses_file_name_then_mime_type():
    media = SimpleNamespace(mime_type="image/jpeg")
    assert media_extension("photo", media, "holiday.png") == ".png"
    assert media_extension("photo", media, None) == ".jpg"


def test_build_destination_sanitizes_and_is_bounded(tmp_path: Path):
    message = SimpleNamespace(
        id=42,
        date=datetime(2025, 3, 9, 12, 30, tzinfo=UTC),
    )
    media = SimpleNamespace(mime_type="video/mp4")
    destination = build_destination(
        tmp_path,
        ["chat_title", "year", "month", "media_type"],
        "{message_id} - {file_name}",
        "News: Daily",
        "video",
        message,
        "../../clip.mp4",
    )
    assert destination.parent == tmp_path / "News_ Daily" / "2025" / "2025-03" / "video"
    assert destination.name == "42 - clip.mp4"
    assert destination.resolve().is_relative_to(tmp_path.resolve())


def test_build_destination_does_not_escape_root(tmp_path: Path):
    message = SimpleNamespace(id=1, date=datetime(2025, 1, 1, tzinfo=UTC))
    media = SimpleNamespace(mime_type="application/pdf")
    destination = build_destination(
        tmp_path,
        ["chat_title"],
        "{file_name}",
        "..",
        "document",
        message,
        "../escape.pdf",
    )
    assert destination.resolve().is_relative_to(tmp_path.resolve())
    assert ".." not in destination.parts


def test_parse_date_bound_is_utc_aware():
    value = parse_date_bound("2025-01-02")
    assert value is not None
    assert value.tzinfo is UTC
    assert value.hour == 0


def test_parse_proxy_url():
    proxy = TelegramManager.parse_proxy("socks5://user:pass@127.0.0.1:1080")
    assert proxy == {
        "scheme": "socks5",
        "hostname": "127.0.0.1",
        "port": 1080,
        "username": "user",
        "password": "pass",
    }

def test_downloader_snapshot_reports_speed_progress_and_disk(tmp_path: Path):
    settings = SimpleNamespace(download_dir=lambda: tmp_path)
    database = SimpleNamespace(
        recent_downloads=lambda limit: [],
        list_chats=lambda: [
            {"id": 1, "enabled": True},
            {"id": 2, "enabled": False},
        ],
    )
    service = DownloaderService(database, settings, telegram=None)
    now = time.monotonic()
    service._active[1] = {
        "record_id": 1,
        "chat": "Demo",
        "message_id": 10,
        "file_name": "demo.mp4",
        "media_type": "video",
        "downloaded": 5 * 1024 * 1024,
        "total": 10 * 1024 * 1024,
        "status": "downloading",
        "started_at": time.time(),
        "speed": 1024 * 1024,
        "last_sample_at": now,
    }

    snapshot = service.snapshot()

    assert snapshot["summary"] == {
        "active_count": 1,
        "configured_count": 2,
        "enabled_count": 1,
        "total_speed": 1048576.0,
        "total_downloaded": 5 * 1024 * 1024,
        "total_size": 10 * 1024 * 1024,
        "remaining": 5 * 1024 * 1024,
        "percent": 50.0,
        "eta_seconds": 5,
    }
    assert snapshot["active"][0]["percent"] == 50.0
    assert snapshot["active"][0]["speed"] == 1048576.0
    assert snapshot["active"][0]["eta_seconds"] == 5
    assert snapshot["resource"]["download_dir"] == str(tmp_path)
    assert snapshot["resource"]["disk_total"] > 0
    assert 0 <= snapshot["resource"]["disk_percent"] <= 100


@pytest.mark.asyncio
async def test_channel_filter_skips_non_matching_media(tmp_path: Path, monkeypatch):
    chat = {
        "id": 1,
        "chat_ref": "-100123",
        "title": "Filtered",
        "enabled": 1,
        "last_message_id": 0,
        "date_from": None,
        "date_to": None,
        "download_filter": "media_type == 'audio'",
    }
    message = SimpleNamespace(
        id=10,
        date=datetime(2025, 1, 1, tzinfo=UTC),
        caption="",
        text="",
        video=SimpleNamespace(file_name="video.mp4", file_size=1024),
        document=None,
        photo=None,
        audio=None,
        voice=None,
        video_note=None,
        animation=None,
    )

    class FakeClient:
        async def get_chat(self, chat_ref):
            return SimpleNamespace(id=-100123, title="Filtered")

        async def get_chat_history(self, chat_id, **kwargs):
            assert kwargs == {"min_id": 0}
            yield message

    last_messages = []
    database = SimpleNamespace(
        update_chat_metadata=lambda *args, **kwargs: None,
        set_last_message=lambda chat_id, message_id: last_messages.append(
            (chat_id, message_id)
        ),
    )
    settings = SimpleNamespace(media_types=lambda: ["video"], file_formats=lambda: {})
    telegram = SimpleNamespace(get_client=lambda: _async_value(FakeClient()))
    service = DownloaderService(database, settings, telegram)
    service.state = "running"

    async def should_not_download(**kwargs):
        raise AssertionError("filtered media must not be downloaded")

    monkeypatch.setattr(service, "_download_message", should_not_download)
    await service._download_chat(chat)

    assert last_messages == [(1, 10)]


@pytest.mark.asyncio
async def test_download_slot_applies_changed_concurrency_limit(tmp_path: Path, monkeypatch):
    current_limit = {"value": 1}
    active = 0
    max_active = 0
    started = []
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release = asyncio.Event()

    async def fake_download(**kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        started.append(kwargs["message"].id)
        first_started.set()
        if len(started) >= 2:
            second_started.set()
        await release.wait()
        active -= 1
        return True

    settings = SimpleNamespace(max_concurrent_downloads=lambda: current_limit["value"])
    service = DownloaderService(SimpleNamespace(), settings, SimpleNamespace())
    monkeypatch.setattr(service, "_perform_download_message", fake_download)

    async def run(message_id):
        return await service._download_message(
            message=SimpleNamespace(id=message_id),
            media=SimpleNamespace(),
            media_type="video",
            file_name=f"{message_id}.mp4",
            chat_id=1,
            chat_title="Concurrent",
        )

    tasks = [asyncio.create_task(run(1)), asyncio.create_task(run(2))]
    await asyncio.wait_for(first_started.wait(), timeout=1)
    await asyncio.sleep(0.05)
    assert started == [1]
    assert max_active == 1

    current_limit["value"] = 2
    await service.refresh_concurrency_limit()
    await asyncio.wait_for(second_started.wait(), timeout=1)
    assert sorted(started) == [1, 2]
    assert max_active == 2

    release.set()
    assert await asyncio.gather(*tasks) == [True, True]
    assert service._download_slots_in_use == 0


@pytest.mark.asyncio
async def test_download_chat_processes_new_messages_oldest_first(
    tmp_path: Path, monkeypatch
):
    chat = {
        "id": 1,
        "chat_ref": "-100123",
        "title": "Ordered",
        "enabled": 1,
        "last_message_id": 10,
        "date_from": None,
        "date_to": None,
        "download_filter": "",
    }
    messages = [
        SimpleNamespace(id=12, date=datetime(2025, 1, 1, tzinfo=UTC)),
        SimpleNamespace(id=11, date=datetime(2025, 1, 1, tzinfo=UTC)),
    ]

    class FakeClient:
        async def get_chat(self, chat_ref):
            return SimpleNamespace(id=-100123, title="Ordered")

        async def get_chat_history(self, chat_id, **kwargs):
            assert kwargs == {"min_id": 10}
            for message in messages:
                yield message

    last_messages = []
    database = SimpleNamespace(
        update_chat_metadata=lambda *args, **kwargs: None,
        set_last_message=lambda chat_id, message_id: last_messages.append(
            (chat_id, message_id)
        ),
    )
    settings = SimpleNamespace(media_types=lambda: ["video"], file_formats=lambda: {})
    telegram = SimpleNamespace(get_client=lambda: _async_value(FakeClient()))
    service = DownloaderService(database, settings, telegram)
    service.state = "running"

    monkeypatch.setattr(
        "app.downloader.detect_media",
        lambda message: None,
    )
    await service._download_chat(chat)

    assert last_messages == [(1, 11), (1, 12)]


async def _async_value(value):
    return value


def test_build_group_destination_uses_chat_folder_and_original_name(tmp_path: Path):
    message = SimpleNamespace(id=12, date=datetime(2025, 1, 1, tzinfo=UTC))
    media = SimpleNamespace(mime_type="video/mp4")
    destination = build_group_destination(
        tmp_path,
        "Demo: Channel",
        "video",
        media,
        "movie.mp4",
    )
    assert destination.parent == tmp_path / "Demo_ Channel"
    assert destination.name == "movie.mp4"


def test_downloader_history_page_uses_offset_and_total(tmp_path: Path):
    calls = []
    database = SimpleNamespace(
        count_downloads=lambda: 37,
        recent_downloads=lambda limit, offset=0: calls.append((limit, offset)) or [],
        list_chats=lambda: [],
    )
    settings = SimpleNamespace(download_dir=lambda: tmp_path)
    service = DownloaderService(database, settings, telegram=None)

    page = service.history_page(page=3, page_size=10)

    assert page == {
        "items": [],
        "total": 37,
        "page": 3,
        "page_size": 10,
        "pages": 4,
    }
    assert calls == [(10, 20)]
