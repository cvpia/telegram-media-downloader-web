from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.downloader import (
    AUTO_RETRY_FAILED_LIMIT,
    CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    DOWNLOAD_CHUNK_SIZE,
    DownloaderService,
    build_destination,
    build_group_destination,
    detect_media,
    format_is_allowed,
    media_extension,
    partial_download_path,
    resumable_download_offset,
)
from app.schemas import SettingsUpdate, parse_date_bound
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


def test_build_destination_uses_video_caption_then_original_extension(tmp_path: Path):
    message = SimpleNamespace(
        id=43,
        date=datetime(2025, 3, 9, 12, 30, tzinfo=UTC),
        caption="第 43 期 - 频道更新",
        text="",
    )
    media = SimpleNamespace(mime_type="video/mp4")

    destination = build_destination(
        tmp_path,
        ["media_type"],
        "{message_id} - {file_name}",
        "Demo",
        "video",
        message,
        "original-video.webm",
    )

    assert destination.name == "43 - 第 43 期 - 频道更新.webm"


def test_build_destination_uses_caption_placeholder(tmp_path: Path):
    message = SimpleNamespace(
        id=44,
        date=datetime(2025, 3, 9, 12, 30, tzinfo=UTC),
        caption="频道文字标题",
        text="",
    )

    destination = build_destination(
        tmp_path,
        [],
        "{caption}",
        "Demo",
        "video",
        message,
        "fallback.mp4",
    )

    assert destination.name == "频道文字标题.mp4"


def test_filename_template_accepts_caption_placeholder():
    payload = SettingsUpdate(filename_template="{caption}")
    assert payload.filename_template == "{caption}"


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
        download_counts=lambda: {
            "completed": 2,
            "failed": 1,
            "retry_wait": 1,
            "skipped": 1,
        },
        download_traffic_bytes=lambda: 40 * 1024 * 1024,
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
        "total_traffic": 40 * 1024 * 1024,
        "total_size": 10 * 1024 * 1024,
        "remaining": 5 * 1024 * 1024,
        "percent": 50.0,
        "eta_seconds": 5,
        "completed_count": 2,
        "failed_count": 1,
        "retry_wait_count": 1,
        "terminal_count": 4,
    }
    assert snapshot["active"][0]["percent"] == 50.0
    assert snapshot["active"][0]["speed"] == 1048576.0
    assert snapshot["active"][0]["eta_seconds"] == 5
    assert snapshot["resource"]["download_dir"] == str(tmp_path)
    assert snapshot["resource"]["disk_total"] > 0
    assert 0 <= snapshot["resource"]["disk_percent"] <= 100


def test_downloader_snapshot_marks_stalled_transfer_as_waiting(tmp_path: Path):
    settings = SimpleNamespace(download_dir=lambda: tmp_path)
    database = SimpleNamespace(
        recent_downloads=lambda limit: [],
        list_chats=lambda: [],
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
        "last_sample_at": now - 8,
    }

    snapshot = service.snapshot()

    assert snapshot["active"][0]["status"] == "waiting"
    assert snapshot["active"][0]["speed"] == 0.0
    assert snapshot["active"][0]["waiting_seconds"] >= 7


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
        resumable_downloads=lambda chat_id: [],
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
async def test_download_slot_allows_ten_concurrent_downloads(monkeypatch):
    active = 0
    max_active = 0

    async def fake_download(**kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return True

    settings = SimpleNamespace(max_concurrent_downloads=lambda: 10)
    service = DownloaderService(SimpleNamespace(), settings, SimpleNamespace())
    monkeypatch.setattr(service, "_perform_download_message", fake_download)

    tasks = [
        service._download_message(
            message=SimpleNamespace(id=message_id),
            media=SimpleNamespace(),
            media_type="video",
            file_name=f"{message_id}.mp4",
            chat_id=1,
            chat_title="Concurrent",
        )
        for message_id in range(15)
    ]

    assert await asyncio.gather(*tasks) == [True] * 15
    assert max_active == 10


@pytest.mark.asyncio
async def test_refresh_concurrency_updates_telegram_transmission_limit():
    limits = []
    telegram = SimpleNamespace(
        set_max_concurrent_transmissions=lambda limit: limits.append(limit)
    )
    settings = SimpleNamespace(max_concurrent_downloads=lambda: 10)
    service = DownloaderService(SimpleNamespace(), settings, telegram)

    await service.refresh_concurrency_limit()

    assert limits == [10]


@pytest.mark.asyncio
async def test_download_chat_schedules_messages_concurrently(monkeypatch):
    messages = [
        SimpleNamespace(
            id=message_id,
            date=None,
            caption="",
            text="",
            video=SimpleNamespace(file_name=f"{message_id}.mp4", file_size=1024),
            document=None,
            photo=None,
            audio=None,
            voice=None,
            video_note=None,
            animation=None,
        )
        for message_id in (3, 2, 1)
    ]

    class FakeClient:
        async def get_chat(self, chat_ref):
            return SimpleNamespace(id=-100123, title="Concurrent")

        async def get_chat_history(self, chat_id, **kwargs):
            for message in messages:
                yield message

    last_messages = []
    database = SimpleNamespace(
        update_chat_metadata=lambda *args, **kwargs: None,
        resumable_downloads=lambda chat_id: [],
        set_last_message=lambda chat_id, message_id: last_messages.append(
            (chat_id, message_id)
        ),
    )
    settings = SimpleNamespace(media_types=lambda: ["video"], file_formats=lambda: {})
    telegram = SimpleNamespace(get_client=lambda: _async_value(FakeClient()))
    service = DownloaderService(database, settings, telegram)
    service.state = "running"
    active = 0
    max_active = 0

    async def fake_download(**kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.05)
        active -= 1
        return True

    monkeypatch.setattr(service, "_download_message", fake_download)
    await asyncio.wait_for(
        service._download_chat(
            {
                "id": 1,
                "chat_ref": "-100123",
                "title": "Concurrent",
                "last_message_id": 0,
                "date_from": None,
                "date_to": None,
                "download_filter": "",
            }
        ),
        timeout=1,
    )

    assert max_active == 3
    assert last_messages == [(1, 3)]


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
        resumable_downloads=lambda chat_id: [],
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

    assert last_messages == [(1, 12)]


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


def test_build_group_destination_uses_video_caption(tmp_path: Path):
    message = SimpleNamespace(
        id=13,
        date=datetime(2025, 1, 1, tzinfo=UTC),
        caption="频道视频标题",
        text="",
    )
    media = SimpleNamespace(mime_type="video/mp4")

    destination = build_group_destination(
        tmp_path,
        "Demo Channel",
        "video",
        message,
        "movie.mp4",
    )

    assert destination.parent == tmp_path / "Demo Channel"
    assert destination.name == "频道视频标题.mp4"


def test_downloader_history_page_uses_offset_and_total(tmp_path: Path):
    calls = []
    database = SimpleNamespace(
        count_downloads=lambda **kwargs: 37,
        recent_downloads=lambda limit, offset=0, **kwargs: calls.append(
            (
                limit,
                offset,
                kwargs.get("terminal_only"),
                kwargs.get("statuses"),
            )
        )
        or [],
        download_counts=lambda: {"completed": 20, "skipped": 5, "failed": 12},
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
        "status": "all",
        "counts": {
            "all": 37,
            "success": 25,
            "failed": 12,
        },
    }
    assert calls == [(10, 20, True, None)]


def test_downloader_history_page_filters_failed_records(tmp_path: Path):
    calls = []
    database = SimpleNamespace(
        count_downloads=lambda **kwargs: calls.append(kwargs) or 4,
        recent_downloads=lambda limit, offset=0, **kwargs: calls.append(
            (limit, offset, kwargs)
        )
        or [],
        download_counts=lambda: {"completed": 8, "failed": 4},
        list_chats=lambda: [],
    )
    service = DownloaderService(
        database,
        SimpleNamespace(download_dir=lambda: tmp_path),
        telegram=None,
    )

    page = service.history_page(page=1, page_size=10, status_filter="failed")

    assert page["status"] == "failed"
    assert page["total"] == 4
    assert page["counts"] == {"all": 12, "success": 8, "failed": 4}
    assert calls[0] == {
        "terminal_only": True,
        "statuses": ("failed",),
    }
    assert calls[1] == (
        10,
        0,
        {"terminal_only": True, "statuses": ("failed",)},
    )


def test_resumable_download_offset_aligns_partial_file(tmp_path: Path):
    partial = tmp_path / "video.mp4.temp"
    partial.write_bytes(b"x" * (DOWNLOAD_CHUNK_SIZE + 321))

    offset = resumable_download_offset(partial, DOWNLOAD_CHUNK_SIZE * 2)

    assert offset == DOWNLOAD_CHUNK_SIZE
    assert partial.stat().st_size == DOWNLOAD_CHUNK_SIZE


@pytest.mark.asyncio
async def test_download_resumes_from_partial_chunk(tmp_path: Path, monkeypatch):
    payload = b"a" * DOWNLOAD_CHUNK_SIZE + b"tail"
    destination = tmp_path / "video.mp4"
    partial = partial_download_path(destination)
    partial.write_bytes(payload[:DOWNLOAD_CHUNK_SIZE])
    calls = []
    updates = []

    class FakeClient:
        async def get_file(self, file_id, file_size, offset, limit=0):
            calls.append((file_id, file_size, offset, limit))
            yield payload[offset * DOWNLOAD_CHUNK_SIZE :]

    database = SimpleNamespace(
        create_download=lambda *args: 7,
        update_download=lambda download_id, **kwargs: updates.append(
            (download_id, kwargs)
        ),
    )
    telegram = SimpleNamespace(get_client=lambda: _async_value(FakeClient()))
    service = DownloaderService(database, SimpleNamespace(), telegram)
    monkeypatch.setattr(
        "app.downloader.FileId.decode", lambda file_id: f"decoded:{file_id}"
    )

    success = await service._perform_download_message(
        message=SimpleNamespace(id=42),
        media=SimpleNamespace(file_id="telegram-file", file_size=len(payload)),
        media_type="video",
        file_name="video.mp4",
        chat_id=1,
        chat_title="Resume Demo",
        destination_override=destination,
    )

    assert success is True
    assert calls == [("decoded:telegram-file", len(payload), 1, 1)]
    assert destination.read_bytes() == payload
    assert not partial.exists()
    assert any(update[1].get("status") == "completed" for update in updates)


@pytest.mark.asyncio
async def test_failed_download_keeps_partial_for_resume(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.downloader.DOWNLOAD_MAX_ATTEMPTS", 1)
    destination = tmp_path / "video.mp4"
    partial = partial_download_path(destination)
    updates = []

    class FakeClient:
        async def get_file(self, file_id, file_size, offset, limit=0):
            yield b"partial-data"
            raise RuntimeError("connection lost")

    database = SimpleNamespace(
        create_download=lambda *args: 8,
        update_download=lambda download_id, **kwargs: updates.append(
            (download_id, kwargs)
        ),
    )
    telegram = SimpleNamespace(get_client=lambda: _async_value(FakeClient()))
    service = DownloaderService(database, SimpleNamespace(), telegram)
    monkeypatch.setattr("app.downloader.FileId.decode", lambda file_id: file_id)

    success = await service._perform_download_message(
        message=SimpleNamespace(id=43),
        media=SimpleNamespace(file_id="telegram-file", file_size=20),
        media_type="video",
        file_name="video.mp4",
        chat_id=1,
        chat_title="Resume Demo",
        destination_override=destination,
    )

    assert success is False
    assert partial.read_bytes() == b"partial-data"
    assert not destination.exists()
    assert any(update[1].get("status") == "failed" for update in updates)
    assert service.last_error


@pytest.mark.asyncio
async def test_download_requests_one_telegram_chunk_at_a_time(
    tmp_path: Path, monkeypatch
):
    payload = (
        b"a" * DOWNLOAD_CHUNK_SIZE
        + b"b" * DOWNLOAD_CHUNK_SIZE
        + b"tail"
    )
    destination = tmp_path / "video.mp4"
    calls = []
    updates = []

    class FakeClient:
        async def get_file(self, file_id, file_size, offset, limit=0):
            calls.append((offset, limit))
            start = offset * DOWNLOAD_CHUNK_SIZE
            yield payload[start : start + DOWNLOAD_CHUNK_SIZE]

    database = SimpleNamespace(
        create_download=lambda *args: 10,
        update_download=lambda download_id, **kwargs: updates.append(
            (download_id, kwargs)
        ),
    )
    telegram = SimpleNamespace(get_client=lambda: _async_value(FakeClient()))
    service = DownloaderService(
        database,
        SimpleNamespace(max_concurrent_downloads=lambda: 3),
        telegram,
    )
    monkeypatch.setattr("app.downloader.FileId.decode", lambda file_id: file_id)

    success = await service._perform_download_message(
        message=SimpleNamespace(id=44),
        media=SimpleNamespace(file_id="telegram-file", file_size=len(payload)),
        media_type="video",
        file_name="video.mp4",
        chat_id=1,
        chat_title="Chunked",
        destination_override=destination,
    )

    assert success is True
    assert calls == [(0, 1), (1, 1), (2, 1)]
    assert destination.read_bytes() == payload
    assert any(update[1].get("status") == "completed" for update in updates)


@pytest.mark.asyncio
async def test_stalled_telegram_chunk_stops_and_keeps_partial(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr("app.downloader.DOWNLOAD_MAX_ATTEMPTS", 1)
    destination = tmp_path / "video.mp4"
    updates = []
    monkeypatch.setattr("app.downloader.TELEGRAM_TRANSFER_STALL_TIMEOUT", 0.01)

    class FakeClient:
        async def get_file(self, file_id, file_size, offset, limit=0):
            await asyncio.sleep(1)
            yield b"late-data"

    database = SimpleNamespace(
        create_download=lambda *args: 11,
        update_download=lambda download_id, **kwargs: updates.append(
            (download_id, kwargs)
        ),
    )
    telegram = SimpleNamespace(get_client=lambda: _async_value(FakeClient()))
    service = DownloaderService(database, SimpleNamespace(), telegram)
    monkeypatch.setattr("app.downloader.FileId.decode", lambda file_id: file_id)

    success = await service._perform_download_message(
        message=SimpleNamespace(id=45),
        media=SimpleNamespace(file_id="telegram-file", file_size=20),
        media_type="video",
        file_name="video.mp4",
        chat_id=1,
        chat_title="Stalled",
        destination_override=destination,
    )

    assert success is False
    assert not destination.exists()
    failed_updates = [
        update[1] for update in updates if update[1].get("status") == "failed"
    ]
    assert failed_updates
    assert "未返回媒体数据" in failed_updates[-1]["error"]


@pytest.mark.asyncio
async def test_resume_incomplete_downloads_uses_saved_destination(
    tmp_path: Path, monkeypatch
):
    destination = tmp_path / "existing.mp4"
    record = {
        "id": 9,
        "message_id": 99,
        "file_name": str(destination),
    }
    message = SimpleNamespace(id=99)
    media = SimpleNamespace(file_name="existing.mp4", file_size=10, file_id="file")
    calls = []

    class FakeClient:
        async def get_messages(self, chat_ref, message_ids):
            calls.append(("get_messages", chat_ref, message_ids))
            return message

    database = SimpleNamespace(resumable_downloads=lambda chat_id: [record])
    settings = SimpleNamespace(download_dir=lambda: tmp_path)
    service = DownloaderService(database, settings, SimpleNamespace())
    service.state = "running"
    monkeypatch.setattr("app.downloader.detect_media", lambda value: ("video", media))

    async def fake_download(**kwargs):
        calls.append(("download", kwargs))
        return True

    monkeypatch.setattr(service, "_download_message", fake_download)

    resumed = await service._resume_incomplete_downloads(
        client=FakeClient(),
        chat={"id": 1},
        chat_ref="@resume",
        chat_title="Resume Demo",
    )

    assert resumed is True
    assert calls[0] == ("get_messages", "@resume", 99)
    assert calls[1][0] == "download"
    assert calls[1][1]["destination_override"] == destination


@pytest.mark.asyncio
async def test_start_bot_download_uses_bot_client(tmp_path: Path, monkeypatch):
    captured = {}
    message = SimpleNamespace(
        id=88,
        date=datetime(2025, 1, 1, tzinfo=UTC),
        chat=SimpleNamespace(id=123),
        caption="",
        text="",
        audio=None,
        document=None,
        photo=None,
        video=SimpleNamespace(file_name="bot-video.mp4", file_size=10),
        voice=None,
        video_note=None,
        animation=None,
    )
    database = SimpleNamespace(
        ensure_download_chat=lambda chat_ref, title: {"id": 5}
    )
    settings = SimpleNamespace(download_dir=lambda: tmp_path)
    service = DownloaderService(database, settings, SimpleNamespace())

    async def fake_run_direct_download(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(service, "_run_direct_download", fake_run_direct_download)
    bot_client = SimpleNamespace(name="bot-client")

    result = await service.start_bot_download(message, bot_client, "Bot 收件箱")
    await asyncio.sleep(0)

    assert result["status"] == "queued"
    assert captured["client"] is bot_client
    assert captured["message"] is message

@pytest.mark.asyncio
async def test_download_retries_transient_empty_chunk_and_refreshes_message(
    tmp_path: Path, monkeypatch
):
    payload = b"retry-success"
    destination = tmp_path / "retry.mp4"
    updates = []
    monkeypatch.setattr("app.downloader.DOWNLOAD_RETRY_DELAYS", (0.0,))

    media = SimpleNamespace(
        file_id="stale-file", file_size=len(payload), file_name="retry.mp4"
    )
    message = SimpleNamespace(id=46, video=media)
    fresh_message = SimpleNamespace(
        id=46,
        video=SimpleNamespace(
            file_id="fresh-file", file_size=len(payload), file_name="retry.mp4"
        ),
    )

    class FakeClient:
        def __init__(self):
            self.file_calls = 0
            self.refresh_calls = 0

        async def get_messages(self, chat_ref, message_ids):
            self.refresh_calls += 1
            assert chat_ref == "@retry"
            assert message_ids == 46
            return fresh_message

        async def get_file(self, file_id, file_size, offset, limit=0):
            self.file_calls += 1
            if self.file_calls == 1:
                return
            assert offset == 0
            yield payload

    client = FakeClient()
    database = SimpleNamespace(
        create_download=lambda *args: 21,
        update_download=lambda download_id, **kwargs: updates.append(
            (download_id, kwargs)
        ),
    )
    service = DownloaderService(database, SimpleNamespace(), SimpleNamespace())
    monkeypatch.setattr("app.downloader.FileId.decode", lambda file_id: file_id)

    success = await service._perform_download_message(
        message=message,
        media=media,
        media_type="video",
        file_name="retry.mp4",
        chat_id=1,
        chat_title="Retry",
        destination_override=destination,
        client_override=client,
        chat_ref="@retry",
    )

    assert success is True
    assert client.file_calls == 2
    assert client.refresh_calls == 1
    assert destination.read_bytes() == payload
    assert any(update[1].get("status") == "retry_wait" for update in updates)
    assert any(update[1].get("status") == "completed" for update in updates)


def test_circuit_breaker_pauses_after_consecutive_terminal_failures():
    service = DownloaderService(SimpleNamespace(), SimpleNamespace(), None)
    service.state = "running"

    for index in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        service._record_terminal_failure(f"failure-{index}")

    assert service.state == "paused"
    assert "已自动暂停" in service.last_error
    service._record_terminal_success()
    assert service._consecutive_terminal_failures == 0


@pytest.mark.asyncio
async def test_run_retries_failed_downloads_after_normal_queue(monkeypatch):
    chat = {"id": 1, "title": "Demo", "enabled": 1}
    requeue_limits = []

    def requeue_failed(limit):
        requeue_limits.append(limit)
        return 2

    database = SimpleNamespace(
        list_chats=lambda: [chat],
        requeue_failed_downloads=requeue_failed,
    )
    service = DownloaderService(database, SimpleNamespace(), telegram=None)
    service.state = "running"
    calls = []

    async def fake_download_chat(received_chat):
        calls.append(
            {
                "chat": received_chat,
                "current_chat": service.current_chat,
                "state": service.state,
            }
        )

    monkeypatch.setattr(service, "_download_chat", fake_download_chat)

    await service._run()

    assert len(calls) == 2
    assert requeue_limits == [AUTO_RETRY_FAILED_LIMIT]
    assert calls[1]["current_chat"] == "失败资源重试"
    assert service.state == "completed"


@pytest.mark.asyncio
async def test_run_does_not_retry_failed_downloads_after_pause(monkeypatch):
    chat = {"id": 1, "title": "Demo", "enabled": 1}
    requeue_limits = []
    database = SimpleNamespace(
        list_chats=lambda: [chat],
        requeue_failed_downloads=lambda limit: requeue_limits.append(limit) or 1,
    )
    service = DownloaderService(database, SimpleNamespace(), telegram=None)
    service.state = "running"

    async def fake_download_chat(received_chat):
        service.state = "paused"

    monkeypatch.setattr(service, "_download_chat", fake_download_chat)

    await service._run()

    assert requeue_limits == []
    assert service.state == "paused"


@pytest.mark.asyncio
async def test_download_chat_stops_after_failed_batch(monkeypatch):
    monkeypatch.setattr("app.downloader.DOWNLOAD_BATCH_SIZE", 2)
    messages = [
        SimpleNamespace(
            id=message_id,
            date=None,
            video=SimpleNamespace(file_name=f"{message_id}.mp4", file_size=10),
        )
        for message_id in (6, 5, 4, 3, 2, 1)
    ]

    class FakeClient:
        async def get_chat(self, chat_ref):
            return SimpleNamespace(id=-100123, title="Batches")

        async def get_chat_history(self, chat_id, **kwargs):
            for message in messages:
                yield message

    last_messages = []
    database = SimpleNamespace(
        update_chat_metadata=lambda *args, **kwargs: None,
        resumable_downloads=lambda chat_id: [],
        set_last_message=lambda chat_id, message_id: last_messages.append(
            (chat_id, message_id)
        ),
    )
    settings = SimpleNamespace(media_types=lambda: ["video"], file_formats=lambda: {})
    telegram = SimpleNamespace(get_client=lambda: _async_value(FakeClient()))
    service = DownloaderService(database, settings, telegram)
    service.state = "running"
    attempted = []

    async def fake_download(**kwargs):
        attempted.append(kwargs["message"].id)
        return False

    monkeypatch.setattr(service, "_download_message", fake_download)
    await service._download_chat(
        {
            "id": 1,
            "chat_ref": "-100123",
            "title": "Batches",
            "last_message_id": 0,
            "date_from": None,
            "date_to": None,
            "download_filter": "",
        }
    )

    assert attempted
    assert set(attempted).issubset({5, 6})
    assert last_messages == []
