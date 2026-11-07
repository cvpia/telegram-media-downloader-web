"""Asynchronous media download service built on Pyrogram."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.database import Database
from app.download_filter import build_filter_context, compile_download_filter
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


def build_group_destination(
    root: Path,
    chat_title: str,
    media_type: str,
    message: Any,
    file_name: str | None,
) -> Path:
    extension = media_extension(media_type, message, file_name)
    original_name = file_name or f"{media_type}_{message.id}{extension}"
    original_path = Path(original_name)
    stem = original_path.stem or f"{media_type}_{message.id}"
    clean_name = sanitize_component(
        f"{stem}{extension}", fallback=f"{getattr(message, 'id', 'file')}{extension}"
    )
    return safe_destination(root, [chat_title or "unknown_chat"], clean_name)


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
        self._direct_tasks: set[asyncio.Task[None]] = set()

    @staticmethod
    def _active_snapshot(item: dict[str, Any], now: float | None = None) -> dict[str, Any]:
        now = time.monotonic() if now is None else now
        item = dict(item)
        total = max(0, int(item.get("total") or 0))
        downloaded = max(0, int(item.get("downloaded") or 0))
        if total:
            downloaded = min(downloaded, total)
        speed = max(0.0, float(item.get("speed") or 0))
        sample_at = float(item.get("last_sample_at") or now)
        if now - sample_at > 3:
            speed = 0.0
        remaining = max(0, total - downloaded)
        item.update(
            {
                "percent": round(downloaded / total * 100, 1) if total else 0.0,
                "speed": round(speed, 1),
                "remaining": remaining,
                "eta_seconds": (
                    round(remaining / speed) if speed > 0 and remaining else None
                ),
            }
        )
        return item

    def _resource_snapshot(self) -> dict[str, Any]:
        download_dir = self.settings.download_dir()
        resource: dict[str, Any] = {
            "download_dir": str(download_dir),
            "disk_total": 0,
            "disk_used": 0,
            "disk_free": 0,
            "disk_percent": 0.0,
        }
        try:
            usage = shutil.disk_usage(download_dir)
        except OSError:
            return resource
        resource.update(
            {
                "disk_total": usage.total,
                "disk_used": usage.used,
                "disk_free": usage.free,
                "disk_percent": round(usage.used / usage.total * 100, 1)
                if usage.total
                else 0.0,
            }
        )
        return resource

    def snapshot(self) -> dict[str, Any]:
        recent = self.database.recent_downloads(50)
        chats = self.database.list_chats()
        now = time.monotonic()
        active = [self._active_snapshot(item, now) for item in self._active.values()]
        total_downloaded = sum(int(item.get("downloaded") or 0) for item in active)
        total_size = sum(int(item.get("total") or 0) for item in active)
        total_speed = sum(float(item.get("speed") or 0) for item in active)
        remaining = sum(int(item.get("remaining") or 0) for item in active)
        return {
            "state": self.state,
            "current_chat": self.current_chat,
            "last_error": self.last_error,
            "active": active,
            "recent": recent,
            "chats": chats,
            "summary": {
                "active_count": len(active),
                "configured_count": len(chats),
                "enabled_count": sum(bool(chat.get("enabled")) for chat in chats),
                "total_speed": round(total_speed, 1),
                "total_downloaded": total_downloaded,
                "total_size": total_size,
                "remaining": remaining,
                "percent": round(total_downloaded / total_size * 100, 1)
                if total_size
                else 0.0,
                "eta_seconds": (
                    round(remaining / total_speed)
                    if total_speed > 0 and remaining
                    else None
                ),
            },
            "resource": self._resource_snapshot(),
        }

    def history_page(self, page: int = 1, page_size: int = 10) -> dict[str, Any]:
        page_size = max(1, min(int(page_size), 50))
        total = self.database.count_downloads()
        pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(int(page), pages))
        items = self.database.recent_downloads(
            page_size, offset=(page - 1) * page_size
        )
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages,
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
        for task in list(self._direct_tasks):
            task.cancel()
        if self._direct_tasks:
            await asyncio.gather(*self._direct_tasks, return_exceptions=True)
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

    async def start_direct_download(
        self,
        dialog_id: str,
        message_id: int,
        chat_title: str = "",
    ) -> dict[str, Any]:
        client = await self.telegram.get_client()
        message = await client.get_messages(dialog_id, message_ids=message_id)
        if isinstance(message, list):
            message = message[0] if message else None
        if message is None:
            raise ValueError("消息不存在或已被删除")
        detected = detect_media(message)
        if detected is None:
            raise ValueError("该消息不包含可下载媒体")
        media_type, media = detected
        file_name = getattr(media, "file_name", None)
        resolved_title = (
            chat_title
            or getattr(getattr(message, "chat", None), "title", None)
            or getattr(getattr(message, "chat", None), "username", None)
            or str(dialog_id)
        )
        hidden_chat = self.database.ensure_download_chat(
            str(dialog_id), str(resolved_title)
        )
        destination = build_group_destination(
            self.settings.download_dir(),
            str(resolved_title),
            media_type,
            message,
            file_name,
        )
        task = asyncio.create_task(
            self._run_direct_download(
                message=message,
                media=media,
                media_type=media_type,
                file_name=file_name,
                chat_id=int(hidden_chat["id"]),
                chat_title=str(resolved_title),
                destination=destination,
            ),
            name=f"telegram-direct-download-{message_id}",
        )
        self._direct_tasks.add(task)
        task.add_done_callback(self._direct_tasks.discard)
        return {
            "ok": True,
            "status": "queued",
            "message_id": message_id,
            "media_type": media_type,
            "chat_title": str(resolved_title),
            "file_name": destination.name,
            "relative_path": str(destination.relative_to(self.settings.download_dir())),
        }

    async def _run_direct_download(
        self,
        *,
        message: Any,
        media: Any,
        media_type: str,
        file_name: str | None,
        chat_id: int,
        chat_title: str,
        destination: Path,
    ) -> None:
        try:
            await self._download_message(
                message=message,
                media=media,
                media_type=media_type,
                file_name=file_name,
                chat_id=chat_id,
                chat_title=chat_title,
                destination_override=destination,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Direct download failed")
            self.last_error = f"{chat_title} / {message.id}: {exc}"

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
        try:
            channel_filter = compile_download_filter(chat.get("download_filter"))
        except ValueError as exc:
            raise ValueError(f"频道“{title}”的下载规则无效: {exc}") from exc

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

            filter_context = build_filter_context(
                message, media_type, media, file_name, extension
            )
            if not channel_filter.matches(filter_context):
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
        destination_override: Path | None = None,
    ) -> bool:
        destination = destination_override or build_destination(
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
            "media_type": media_type,
            "downloaded": 0,
            "total": total_size,
            "status": "downloading",
            "started_at": time.time(),
            "updated_at": time.time(),
            "speed": 0.0,
            "last_sample_at": time.monotonic(),
            "last_sample_bytes": 0,
        }
        async with self._active_lock:
            self._active[record_id] = active

        last_update = 0.0

        async def progress(current: int, total: int, *_: Any) -> None:
            nonlocal last_update
            now = time.monotonic()
            active["downloaded"] = int(current)
            active["total"] = int(total or total_size)
            active["updated_at"] = time.time()
            if now - last_update >= 0.5:
                elapsed = now - float(active["last_sample_at"])
                delta = int(current) - int(active["last_sample_bytes"])
                if elapsed > 0:
                    active["speed"] = max(0.0, delta / elapsed)
                active["last_sample_at"] = now
                active["last_sample_bytes"] = int(current)
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