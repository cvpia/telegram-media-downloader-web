"""Asynchronous media download service built on Pyrogram."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.database import Database
from app.schemas import parse_date_bound
from app.settings_service import SettingsService
from app.telegram_manager import TelegramManager


logger = logging.getLogger(__name__)

MEDIA_TYPES = ("audio", "document", "photo", "video", "voice", "video_note", "animation")
FORMAT_FILTERED_TYPES = {"audio", "document", "video", "animation"}
EXTENSION_OVERRIDES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "application/pdf": ".pdf",
}
INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def detect_media(message: Any) -> tuple[str, Any] | None:
    for media_type in MEDIA_TYPES:
        media = getattr(message, media_type, None)
        if media is not None:
            return media_type, media
    return None


def media_extension(media_type: str, media: Any, file_name: str | None) -> str:
    if file_name:
        suffix = Path(file_name).suffix
        if suffix:
            return suffix.lower()
    mime_type = str(getattr(media, "mime_type", "") or "").lower()
    if mime_type in EXTENSION_OVERRIDES:
        return EXTENSION_OVERRIDES[mime_type]
    guessed = mimetypes.guess_extension(mime_type.split(";", 1)[0].strip()) if mime_type else None
    if guessed:
        return guessed.lower()
    return {
        "photo": ".jpg",
        "voice": ".ogg",
        "video_note": ".mp4",
        "video": ".mp4",
        "audio": ".mp3",
        "animation": ".mp4",
        "document": ".bin",
    }.get(media_type, ".bin")


def format_is_allowed(
    media_type: str,
    extension: str,
    configured: dict[str, list[str]],
) -> bool:
    if media_type not in FORMAT_FILTERED_TYPES:
        return True
    allowed = [str(item).lower().lstrip(".") for item in configured.get(media_type, ["all"])]
    return "all" in allowed or extension.lower().lstrip(".") in allowed


def sanitize_component(value: str, fallback: str = "untitled") -> str:
    value = INVALID_PATH_CHARS.sub("_", value).strip().strip(".")
    value = re.sub(r"\s+", " ", value)
    if not value:
        value = fallback
    return value[:180]


def safe_destination(root: Path, parts: list[str], file_name: str) -> Path:
    root = root.resolve()
    candidate = root.joinpath(*[sanitize_component(part) for part in parts], file_name)
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("目标路径越界") from exc
    return resolved


def build_destination(
    root: Path,
    folder_parts: list[str],
    filename_template: str,
    chat_title: str,
    media_type: str,
    message: Any,
    file_name: str | None,
) -> Path:
    message_date = message.date or datetime.now(UTC)
    if isinstance(message_date, str):
        message_date = datetime.fromisoformat(message_date)
    date_values = {
        "year": message_date.strftime("%Y"),
        "month": message_date.strftime("%Y-%m"),
        "date": message_date.strftime("%Y-%m-%d"),
    }
    parts: list[str] = []
    for part in folder_parts:
        if part == "chat_title":
            parts.append(chat_title or "unknown_chat")
        elif part == "media_type":
            parts.append(media_type)
        else:
            parts.append(date_values[part])

    original_name = file_name or f"{media_type}_{message.id}"
    stem = Path(original_name).stem or f"{media_type}_{message.id}"
    extension = media_extension(media_type, message, file_name)
    rendered = filename_template.format(
        message_id=message.id,
        file_name=stem,
        media_type=media_type,
        date=message_date.strftime("%Y%m%d_%H%M%S"),
    )
    rendered = sanitize_component(rendered, fallback=f"{message.id}")
    return safe_destination(root, parts, f"{rendered}{extension}")


class DownloaderService:
    def __init__(
        self,
        database: Database,
        settings: SettingsService,
        telegram: TelegramManager,
    ):
        self.database = database
        self.settings = settings
        self.telegram = telegram
        self.state = "stopped"
        self.last_error: str | None = None
        self.current_chat: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._active: dict[int, dict[str, Any]] = {}
        self._active_lock = asyncio.Lock()

    def snapshot(self) -> dict[str, Any]:
        recent = self.database.recent_downloads(50)
        return {
            "state": self.state,
            "current_chat": self.current_chat,
            "last_error": self.last_error,
            "active": list(self._active.values()),
            "recent": recent,
            "chats": self.database.list_chats(),
        }

    async def start(self) -> dict[str, Any]:
        if self._task and not self._task.done():
            return self.snapshot()
        if not self.settings.telegram_connected:
            raise ValueError("请先完成 Telegram 登录")
        chats = [chat for chat in self.database.list_chats() if chat["enabled"]]
        if not chats:
            raise ValueError("请至少添加一个启用的下载频道")
        self.state = "running"
        self.last_error = None
        self._task = asyncio.create_task(self._run(), name="telegram-downloader")
        return self.snapshot()

    async def pause(self) -> dict[str, Any]:
        if self.state == "running":
            self.state = "paused"
        return self.snapshot()

    async def resume(self) -> dict[str, Any]:
        if self.state == "paused":
            self.state = "running"
        elif self.state == "stopped":
            return await self.start()
        return self.snapshot()

    async def stop(self) -> dict[str, Any]:
        self.state = "stopped"
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.current_chat = None
        return self.snapshot()

    async def close(self) -> None:
        await self.stop()

    async def _run(self) -> None:
        try:
            max_concurrent = self.settings.max_concurrent_downloads()
            semaphore = asyncio.Semaphore(max_concurrent)
            chats = [chat for chat in self.database.list_chats() if chat["enabled"]]

            async def run_chat(chat: dict[str, Any]) -> None:
                async with semaphore:
                    await self._download_chat(chat)

            await asyncio.gather(*(run_chat(chat) for chat in chats))
            if self.state != "stopped":
                self.state = "completed"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Downloader stopped with an error")
            self.last_error = str(exc)
            self.state = "error"
        finally:
            self.current_chat = None
            self._active.clear()

    async def _wait_if_paused(self) -> None:
        while self.state == "paused":
            await asyncio.sleep(0.5)
        if self.state == "stopped":
            raise asyncio.CancelledError

    async def _download_chat(self, chat: dict[str, Any]) -> None:
        client = await self.telegram.get_client()
        chat_ref: int | str = chat["chat_ref"]
        if isinstance(chat_ref, str) and re.fullmatch(r"-?\d+", chat_ref):
            chat_ref = int(chat_ref)

        telegram_chat = await client.get_chat(chat_ref)
        title = getattr(telegram_chat, "title", None) or getattr(telegram_chat, "username", None) or chat["title"] or str(chat_ref)
        self.database.update_chat_metadata(chat["id"], title=str(title))
        self.current_chat = str(title)

        date_from = parse_date_bound(chat.get("date_from"))
        date_to = parse_date_bound(chat.get("date_to"), end_of_day=True)
        media_types = set(self.settings.media_types())
        file_formats = self.settings.file_formats()

        async for message in client.get_chat_history(
            telegram_chat.id,
            offset_id=int(chat["last_message_id"]),
            reverse=True,
        ):
            await self._wait_if_paused()
            if message.id <= int(chat["last_message_id"]):
                continue
            message_date = message.date
            if message_date is not None:
                if message_date.tzinfo is None:
                    message_date = message_date.replace(tzinfo=UTC)
                if date_from and message_date < date_from:
                    continue
                if date_to and message_date > date_to:
                    continue

            detected = detect_media(message)
            if detected is None:
                self.database.set_last_message(chat["id"], message.id)
                continue

            media_type, media = detected
            if media_type not in media_types:
                self.database.set_last_message(chat["id"], message.id)
                continue

            file_name = getattr(media, "file_name", None)
            extension = media_extension(media_type, media, file_name)
            if not format_is_allowed(media_type, extension, file_formats):
                self.database.set_last_message(chat["id"], message.id)
                continue

            success = await self._download_message(
                message=message,
                media=media,
                media_type=media_type,
                file_name=file_name,
                chat_id=chat["id"],
                chat_title=str(title),
            )
            if not success:
                return
            self.database.set_last_message(chat["id"], message.id)

    async def _download_message(
        self,
        *,
        message: Any,
        media: Any,
        media_type: str,
        file_name: str | None,
        chat_id: int,
        chat_title: str,
    ) -> bool:
        destination = build_destination(
            self.settings.download_dir(),
            self.settings.folder_parts(),
            self.settings.filename_template(),
            chat_title,
            media_type,
            message,
            file_name,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        total_size = int(getattr(media, "file_size", 0) or 0)
        record_id = self.database.create_download(
            chat_id,
            chat_title,
            message.id,
            str(destination),
            media_type,
            total_size,
        )

        if destination.exists() and destination.stat().st_size > 0:
            self.database.update_download(record_id, status="skipped")
            return True

        active = {
            "record_id": record_id,
            "chat": chat_title,
            "message_id": message.id,
            "file_name": destination.name,
            "downloaded": 0,
            "total": total_size,
            "status": "downloading",
            "started_at": time.time(),
        }
        async with self._active_lock:
            self._active[record_id] = active

        last_update = 0.0

        async def progress(current: int, total: int, *_: Any) -> None:
            nonlocal last_update
            now = time.monotonic()
            active["downloaded"] = int(current)
            active["total"] = int(total or total_size)
            if now - last_update >= 0.5:
                self.database.update_download(record_id, downloaded_size=int(current))
                last_update = now

        try:
            result = await message.download(
                file_name=str(destination),
                progress=progress,
                progress_args=(),
            )
            if result is None:
                raise RuntimeError("Telegram 未返回下载文件")
            size = destination.stat().st_size if destination.exists() else int(active["total"])
            self.database.update_download(
                record_id,
                status="completed",
                downloaded_size=size,
                file_name=str(destination),
            )
            return True
        except asyncio.CancelledError:
            self.database.update_download(
                record_id, status="failed", error="下载已停止"
            )
            raise
        except Exception as exc:
            logger.exception("Failed to download message %s", message.id)
            self.database.update_download(record_id, status="failed", error=str(exc))
            self.last_error = f"{chat_title} / {message.id}: {exc}"
            return False
        finally:
            async with self._active_lock:
                self._active.pop(record_id, None)