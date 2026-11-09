"""Asynchronous media download service built on Pyrogram."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import re
import shutil
import time
from contextlib import aclosing, asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pyrogram.file_id import FileId

from app.database import Database
from app.download_filter import build_filter_context, compile_download_filter
from app.schemas import parse_date_bound
from app.settings_service import SettingsService
from app.telegram_manager import TelegramManager
from app.telegram_transfer import AdaptiveTransferGate


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
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
TELEGRAM_TRANSFER_STALL_TIMEOUT = 45.0
DOWNLOAD_BATCH_SIZE = 24
DOWNLOAD_MAX_ATTEMPTS = 4
DOWNLOAD_RETRY_DELAYS = (2.0, 8.0, 25.0)
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 5
DOWNLOAD_PROGRESS_PERSIST_SECONDS = 2.0
RESOURCE_SNAPSHOT_TTL_SECONDS = 5.0


class PermanentDownloadError(RuntimeError):
    """A failure that retrying the same Telegram message cannot resolve."""


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


def message_caption(message: Any) -> str:
    """Return the visible message text used for media naming."""
    return str(
        getattr(message, "caption", None)
        or getattr(message, "text", None)
        or ""
    ).strip()


def media_name_stem(media_type: str, message: Any, file_name: str | None) -> str:
    """Prefer the channel message text for videos, then the original file name."""
    original_name = file_name or f"{media_type}_{getattr(message, 'id', 'file')}"
    original_stem = Path(original_name).stem or (
        f"{media_type}_{getattr(message, 'id', 'file')}"
    )
    if media_type == "video":
        caption = message_caption(message)
        if caption:
            return caption
    return original_stem


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


def partial_download_path(destination: Path) -> Path:
    return destination.with_name(f"{destination.name}.temp")


def resumable_download_offset(partial: Path, total_size: int = 0) -> int:
    """Return a Telegram chunk-aligned offset while keeping the partial file."""
    try:
        size = partial.stat().st_size
    except OSError:
        return 0
    if total_size > 0:
        size = min(size, total_size)
    offset = size - (size % DOWNLOAD_CHUNK_SIZE)
    try:
        with partial.open("r+b") as file:
            file.truncate(offset)
    except OSError:
        return 0
    return offset


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

    stem = media_name_stem(media_type, message, file_name)
    caption = message_caption(message) or stem
    extension = media_extension(media_type, message, file_name)
    rendered = filename_template.format(
        message_id=message.id,
        file_name=stem,
        caption=caption,
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
    stem = media_name_stem(media_type, message, file_name)
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
        self._concurrency_condition = asyncio.Condition()
        self._download_slots_in_use = 0
        self._direct_tasks: set[asyncio.Task[None]] = set()
        self._consecutive_terminal_failures = 0
        self._resource_cache: dict[str, Any] | None = None
        self._resource_cache_at = 0.0
        self._transfer_gate = AdaptiveTransferGate(
            self._configured_concurrency_limit
        )

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
        if item.get("status") == "retrying":
            retry_at = float(item.get("retry_at") or now)
            item["status"] = "retrying"
            item["retry_seconds"] = round(max(0.0, retry_at - now), 1)
            item["waiting_seconds"] = item["retry_seconds"]
        else:
            waiting = now - sample_at >= 3 and (
                total <= 0 or downloaded < total
            )
            if waiting:
                item["status"] = "waiting"
                item["waiting_seconds"] = round(now - sample_at)
            else:
                item["status"] = "downloading"
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
        now = time.monotonic()
        if (
            self._resource_cache is not None
            and now - self._resource_cache_at < RESOURCE_SNAPSHOT_TTL_SECONDS
        ):
            return dict(self._resource_cache)
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
        self._resource_cache = dict(resource)
        self._resource_cache_at = now
        return resource

    def snapshot(self) -> dict[str, Any]:
        chats = self.database.list_chats()
        counter = getattr(self.database, "download_counts", None)
        counts = counter() if callable(counter) else {}
        now = time.monotonic()
        active = [self._active_snapshot(item, now) for item in self._active.values()]
        total_downloaded = sum(int(item.get("downloaded") or 0) for item in active)
        total_size = sum(int(item.get("total") or 0) for item in active)
        total_speed = sum(float(item.get("speed") or 0) for item in active)
        remaining = sum(int(item.get("remaining") or 0) for item in active)
        terminal_count = sum(
            int(counts.get(status, 0))
            for status in ("completed", "skipped", "failed")
        )
        return {
            "state": self.state,
            "current_chat": self.current_chat,
            "last_error": self.last_error,
            "active": active,
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
                "completed_count": int(counts.get("completed", 0)),
                "failed_count": int(counts.get("failed", 0)),
                "retry_wait_count": int(counts.get("retry_wait", 0)),
                "terminal_count": terminal_count,
            },
            "resource": self._resource_snapshot(),
        }

    def history_page(self, page: int = 1, page_size: int = 10) -> dict[str, Any]:
        page_size = max(1, min(int(page_size), 50))
        total = self.database.count_downloads(terminal_only=True)
        pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(int(page), pages))
        items = self.database.recent_downloads(
            page_size,
            offset=(page - 1) * page_size,
            terminal_only=True,
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
        self._consecutive_terminal_failures = 0
        self._task = asyncio.create_task(self._run(), name="telegram-downloader")
        return self.snapshot()

    async def pause(self) -> dict[str, Any]:
        if self.state == "running":
            self.state = "paused"
        return self.snapshot()

    async def resume(self) -> dict[str, Any]:
        if self.state == "paused" and self._task and not self._task.done():
            self.state = "running"
            return self.snapshot()
        if self.state != "running":
            return await self.start()
        return self.snapshot()

    async def retry_failed(self, limit: int = 100) -> dict[str, Any]:
        if self._task and not self._task.done():
            raise ValueError("请先停止当前下载，再重试失败任务")
        if not self.settings.telegram_connected:
            raise ValueError("请先完成 Telegram 登录")
        if not any(chat["enabled"] for chat in self.database.list_chats()):
            raise ValueError("请至少添加一个启用的下载频道")
        requeue = getattr(self.database, "requeue_failed_downloads", None)
        requeued = requeue(limit) if callable(requeue) else 0
        if not requeued:
            result = self.snapshot()
            result["requeued"] = 0
            return result
        result = await self.start()
        result["requeued"] = requeued
        return result

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

    async def refresh_concurrency_limit(self) -> None:
        limit = self._configured_concurrency_limit()
        update_transmissions = getattr(
            self.telegram, "set_max_concurrent_transmissions", None
        )
        if callable(update_transmissions):
            update_transmissions(limit)
        async with self._concurrency_condition:
            self._concurrency_condition.notify_all()

    @asynccontextmanager
    async def _download_slot(self):
        limit = self._configured_concurrency_limit()
        async with self._concurrency_condition:
            while self._download_slots_in_use >= limit:
                await self._concurrency_condition.wait()
                limit = self._configured_concurrency_limit()
            self._download_slots_in_use += 1
        try:
            yield
        finally:
            async with self._concurrency_condition:
                self._download_slots_in_use = max(0, self._download_slots_in_use - 1)
                self._concurrency_condition.notify_all()

    def _configured_concurrency_limit(self) -> int:
        getter = getattr(self.settings, "max_concurrent_downloads", None)
        value = getter() if callable(getter) else 1
        return max(1, min(int(value), 10))

    def _record_terminal_failure(self, error: str) -> None:
        self._consecutive_terminal_failures += 1
        if (
            self._consecutive_terminal_failures
            < CIRCUIT_BREAKER_FAILURE_THRESHOLD
        ):
            return
        self.state = "paused"
        self.last_error = (
            "连续 "
            f"{self._consecutive_terminal_failures} 个下载任务失败，已自动暂停，"
            f"避免继续产生错误记录。最近错误：{error}"
        )
        logger.error(self.last_error)

    def _record_terminal_success(self) -> None:
        self._consecutive_terminal_failures = 0

    async def _run_download_tasks(
        self, tasks: list[asyncio.Task[bool]]
    ) -> bool:
        """Run a bounded batch and cancel it as soon as one item fails."""
        pending: set[asyncio.Task[bool]] = set(tasks)
        try:
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    try:
                        succeeded = bool(task.result())
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("Download task raised unexpectedly")
                        succeeded = False
                    if succeeded:
                        continue
                    for remaining in pending:
                        remaining.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    return False
            return True
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self) -> None:
        try:
            chats = [chat for chat in self.database.list_chats() if chat["enabled"]]
            for chat in chats:
                await self._wait_if_paused()
                await self._download_chat(chat)
                if self.state in {"paused", "stopped"}:
                    break
            if self.state == "running":
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
                chat_ref=dialog_id,
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
        client: Any | None = None,
        chat_ref: int | str | None = None,
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
                client_override=client,
                chat_ref=chat_ref,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Direct download failed")
            self.last_error = f"{chat_title} / {message.id}: {exc}"

    async def start_bot_download(
        self,
        message: Any,
        client: Any,
        chat_title: str = "Bot 收件箱",
    ) -> dict[str, Any]:
        detected = detect_media(message)
        if detected is None:
            raise ValueError("消息不包含可下载媒体")
        media_type, media = detected
        file_name = getattr(media, "file_name", None)
        hidden_chat = self.database.ensure_download_chat(
            f"bot:{getattr(message.chat, 'id', 'inbox')}", chat_title
        )
        destination = build_group_destination(
            self.settings.download_dir(),
            chat_title,
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
                chat_title=chat_title,
                destination=destination,
                client=client,
                chat_ref=getattr(getattr(message, "chat", None), "id", None),
            ),
            name=f"telegram-bot-download-{getattr(message, 'id', 0)}",
        )
        self._direct_tasks.add(task)
        task.add_done_callback(self._direct_tasks.discard)
        return {
            "ok": True,
            "status": "queued",
            "message_id": getattr(message, "id", 0),
            "media_type": media_type,
            "file_name": destination.name,
        }

    async def _sleep_with_control(self, delay: float, respect_control: bool) -> None:
        if not respect_control:
            await asyncio.sleep(max(0.0, delay))
            return
        remaining = max(0.0, float(delay))
        while remaining > 0:
            if self.state == "stopped":
                raise asyncio.CancelledError
            if self.state == "paused":
                await asyncio.sleep(0.25)
                continue
            interval = min(0.25, remaining)
            await asyncio.sleep(interval)
            remaining -= interval

    async def _wait_if_paused(self) -> None:
        while self.state == "paused":
            await asyncio.sleep(0.5)
        if self.state == "stopped":
            raise asyncio.CancelledError

    async def _resume_incomplete_downloads(
        self,
        *,
        client: Any,
        chat: dict[str, Any],
        chat_ref: int | str,
        chat_title: str,
    ) -> bool:
        records = self.database.resumable_downloads(chat["id"])
        if not records:
            return True
        root = self.settings.download_dir().resolve()
        for offset in range(0, len(records), DOWNLOAD_BATCH_SIZE):
            await self._wait_if_paused()
            tasks: list[asyncio.Task[bool]] = []
            for record in records[offset : offset + DOWNLOAD_BATCH_SIZE]:
                await self._wait_if_paused()
                record_id = int(record["id"])
                destination = Path(str(record.get("file_name", ""))).resolve()
                try:
                    destination.relative_to(root)
                except ValueError:
                    self.database.update_download(
                        record_id,
                        status="failed",
                        error="下载记录路径不在当前下载目录内，无法继续",
                        error_code="InvalidDownloadPath",
                    )
                    continue

                try:
                    message = await client.get_messages(
                        chat_ref, message_ids=int(record["message_id"])
                    )
                except Exception as exc:
                    logger.exception("Unable to reload message for resume")
                    self.database.update_download(
                        record_id,
                        status="retry_wait",
                        attempt_count=max(1, int(record.get("attempt_count") or 0)),
                        error=f"恢复下载失败：{exc}",
                        error_code=exc.__class__.__name__,
                        next_retry_at=None,
                    )
                    self.last_error = f"{chat_title}: 恢复下载失败：{exc}"
                    for task in tasks:
                        task.cancel()
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                    return False
                if message is None:
                    self.database.update_download(
                        record_id,
                        status="failed",
                        error="原 Telegram 消息不存在，无法继续下载",
                        error_code="MessageMissing",
                    )
                    continue

                detected = detect_media(message)
                if detected is None:
                    self.database.update_download(
                        record_id,
                        status="failed",
                        error="原 Telegram 消息已不包含可下载媒体",
                        error_code="MediaMissing",
                    )
                    continue

                media_type, media = detected
                tasks.append(
                    asyncio.create_task(
                        self._download_message(
                            message=message,
                            media=media,
                            media_type=media_type,
                            file_name=getattr(media, "file_name", None),
                            chat_id=chat["id"],
                            chat_title=chat_title,
                            destination_override=destination,
                            client_override=client,
                            chat_ref=chat_ref,
                            previous_attempts=int(record.get("attempt_count") or 0),
                            respect_control=True,
                        ),
                        name=f"resume-{record_id}",
                    )
                )
            if tasks and not await self._run_download_tasks(tasks):
                return False
        return True

    async def _process_history_batch(
        self,
        *,
        batch: list[Any],
        chat: dict[str, Any],
        chat_ref: int | str,
        chat_title: str,
        date_from: Any,
        date_to: Any,
        media_types: set[str],
        file_formats: dict[str, list[str]],
        channel_filter: Any,
    ) -> bool:
        tasks: list[asyncio.Task[bool]] = []
        for message in reversed(batch):
            await self._wait_if_paused()
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
                continue
            media_type, media = detected
            if media_type not in media_types:
                continue

            file_name = getattr(media, "file_name", None)
            extension = media_extension(media_type, media, file_name)
            if not format_is_allowed(media_type, extension, file_formats):
                continue
            filter_context = build_filter_context(
                message, media_type, media, file_name, extension
            )
            if not channel_filter.matches(filter_context):
                continue

            tasks.append(
                asyncio.create_task(
                    self._download_message(
                        message=message,
                        media=media,
                        media_type=media_type,
                        file_name=file_name,
                        chat_id=chat["id"],
                        chat_title=chat_title,
                        chat_ref=chat_ref,
                        respect_control=True,
                    ),
                    name=f"download-{chat['id']}-{message.id}",
                )
            )
        if not tasks:
            return True
        return await self._run_download_tasks(tasks)

    async def _download_chat(self, chat: dict[str, Any]) -> None:
        client = await self.telegram.get_client()
        chat_ref: int | str = chat["chat_ref"]
        if isinstance(chat_ref, str) and re.fullmatch(r"-?\d+", chat_ref):
            chat_ref = int(chat_ref)

        telegram_chat = await client.get_chat(chat_ref)
        title = (
            getattr(telegram_chat, "title", None)
            or getattr(telegram_chat, "username", None)
            or chat["title"]
            or str(chat_ref)
        )
        self.database.update_chat_metadata(chat["id"], title=str(title))
        self.current_chat = str(title)

        if not await self._resume_incomplete_downloads(
            client=client,
            chat=chat,
            chat_ref=chat_ref,
            chat_title=str(title),
        ):
            return

        date_from = parse_date_bound(chat.get("date_from"))
        date_to = parse_date_bound(chat.get("date_to"), end_of_day=True)
        media_types = set(self.settings.media_types())
        file_formats = self.settings.file_formats()
        try:
            channel_filter = compile_download_filter(chat.get("download_filter"))
        except ValueError as exc:
            raise ValueError(f"频道“{title}”的下载规则无效: {exc}") from exc

        last_message_id = int(chat["last_message_id"])
        highest_message_id = last_message_id
        batch: list[Any] = []
        async for message in client.get_chat_history(
            telegram_chat.id,
            min_id=last_message_id,
        ):
            await self._wait_if_paused()
            if message.id <= last_message_id:
                continue
            highest_message_id = max(highest_message_id, int(message.id))
            batch.append(message)
            if len(batch) < DOWNLOAD_BATCH_SIZE:
                continue
            if not await self._process_history_batch(
                batch=batch,
                chat=chat,
                chat_ref=chat_ref,
                chat_title=str(title),
                date_from=date_from,
                date_to=date_to,
                media_types=media_types,
                file_formats=file_formats,
                channel_filter=channel_filter,
            ):
                return
            batch.clear()

        if batch and not await self._process_history_batch(
            batch=batch,
            chat=chat,
            chat_ref=chat_ref,
            chat_title=str(title),
            date_from=date_from,
            date_to=date_to,
            media_types=media_types,
            file_formats=file_formats,
            channel_filter=channel_filter,
        ):
            return
        if highest_message_id > last_message_id:
            self.database.set_last_message(chat["id"], highest_message_id)

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
        client_override: Any | None = None,
        chat_ref: int | str | None = None,
        previous_attempts: int = 0,
        respect_control: bool = False,
    ) -> bool:
        async with self._download_slot():
            return await self._perform_download_message(
                message=message,
                media=media,
                media_type=media_type,
                file_name=file_name,
                chat_id=chat_id,
                chat_title=chat_title,
                destination_override=destination_override,
                client_override=client_override,
                chat_ref=chat_ref,
                previous_attempts=previous_attempts,
                respect_control=respect_control,
            )

    async def _read_telegram_chunk(
        self,
        client: Any,
        decoded_file_id: Any,
        file_size: int,
        chunk_index: int,
    ) -> bytes:
        async def read_one() -> bytes:
            chunks = client.get_file(
                decoded_file_id,
                file_size=file_size,
                limit=1,
                offset=chunk_index,
            )
            async with aclosing(chunks):
                async for chunk in chunks:
                    return bytes(chunk)
            return b""

        try:
            return await asyncio.wait_for(
                read_one(), timeout=TELEGRAM_TRANSFER_STALL_TIMEOUT
            )
        except TimeoutError as exc:
            raise RuntimeError(
                "Telegram 连续 "
                f"{TELEGRAM_TRANSFER_STALL_TIMEOUT:g} 秒未返回媒体数据，"
                "已停止本次下载；临时文件已保留，可稍后继续"
            ) from exc

    async def _perform_download_message(
        self,
        *,
        message: Any,
        media: Any,
        media_type: str,
        file_name: str | None,
        chat_id: int,
        chat_title: str,
        destination_override: Path | None = None,
        client_override: Any | None = None,
        chat_ref: int | str | None = None,
        previous_attempts: int = 0,
        respect_control: bool = False,
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
        partial = partial_download_path(destination)

        def file_size(path: Path) -> int:
            try:
                return path.stat().st_size
            except OSError:
                return 0

        final_size = file_size(destination)
        if final_size > 0 and (total_size <= 0 or final_size >= total_size):
            self.database.update_download(
                record_id,
                status="skipped",
                downloaded_size=final_size,
                error=None,
                error_code=None,
                next_retry_at=None,
            )
            self._record_terminal_success()
            return True

        # Preserve files written directly by older versions so they can resume.
        if final_size > file_size(partial):
            if partial.exists():
                partial.unlink()
            destination.replace(partial)

        partial_size = file_size(partial)
        if total_size > 0 and partial_size >= total_size:
            with partial.open("r+b") as file:
                file.truncate(total_size)
            partial.replace(destination)
            self.database.update_download(
                record_id,
                status="completed",
                downloaded_size=total_size,
                file_name=str(destination),
                error=None,
                error_code=None,
                next_retry_at=None,
            )
            self._record_terminal_success()
            return True

        start_size = resumable_download_offset(partial, total_size)
        max_attempts = max(1, int(DOWNLOAD_MAX_ATTEMPTS))
        attempt = min(max(0, int(previous_attempts)), max_attempts - 1)
        active = {
            "record_id": record_id,
            "chat": chat_title,
            "message_id": message.id,
            "file_name": destination.name,
            "media_type": media_type,
            "downloaded": start_size,
            "total": total_size,
            "status": "downloading",
            "attempt": min(max_attempts, attempt + 1),
            "max_attempts": max_attempts,
            "retry_at": None,
            "retry_seconds": None,
            "error": None,
            "started_at": time.time(),
            "updated_at": time.time(),
            "speed": 0.0,
            "last_sample_at": time.monotonic(),
            "last_sample_bytes": start_size,
            "transfer_limit": self._transfer_gate.current_limit,
        }
        async with self._active_lock:
            self._active[record_id] = active

        downloaded = start_size
        last_update = 0.0
        last_persist = time.monotonic() - DOWNLOAD_PROGRESS_PERSIST_SECONDS
        client = client_override

        def update_progress(force: bool = False) -> None:
            nonlocal last_update, last_persist
            now = time.monotonic()
            if not force and now - last_update < 0.5:
                return
            active["downloaded"] = downloaded
            active["total"] = total_size
            active["updated_at"] = time.time()
            elapsed = now - float(active["last_sample_at"])
            delta = downloaded - int(active["last_sample_bytes"])
            if elapsed > 0:
                active["speed"] = max(0.0, delta / elapsed)
            active["last_sample_at"] = now
            active["last_sample_bytes"] = downloaded
            active["transfer_limit"] = self._transfer_gate.current_limit
            if force or now - last_persist >= DOWNLOAD_PROGRESS_PERSIST_SECONDS:
                self.database.update_download(record_id, downloaded_size=downloaded)
                last_persist = now
            last_update = now

        try:
            while attempt < max_attempts:
                attempt += 1
                try:
                    if client is None:
                        client = await self.telegram.get_client()

                    # File references and peer metadata can expire. Reload the
                    # message immediately before each retry so Telegram issues
                    # a fresh file reference instead of repeating a stale one.
                    if attempt > 1 and chat_ref is not None:
                        refreshed = await client.get_messages(
                            chat_ref, message_ids=message.id
                        )
                        if isinstance(refreshed, list):
                            refreshed = refreshed[0] if refreshed else None
                        if refreshed is None:
                            raise PermanentDownloadError(
                                "原 Telegram 消息不存在，无法继续下载"
                            )
                        detected = detect_media(refreshed)
                        if detected is None:
                            raise PermanentDownloadError(
                                "原 Telegram 消息已不包含可下载媒体"
                            )
                        message = refreshed
                        media_type, media = detected
                        file_name = getattr(media, "file_name", None)
                        total_size = int(getattr(media, "file_size", 0) or 0)

                    start_size = resumable_download_offset(partial, total_size)
                    downloaded = start_size
                    now = time.monotonic()
                    active.update(
                        {
                            "downloaded": downloaded,
                            "total": total_size,
                            "status": "downloading",
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                            "retry_at": None,
                            "retry_seconds": None,
                            "error": None,
                            "speed": 0.0,
                            "last_sample_at": now,
                            "last_sample_bytes": downloaded,
                        }
                    )
                    self.database.update_download(
                        record_id,
                        status="downloading",
                        downloaded_size=downloaded,
                        attempt_count=attempt,
                        error=None,
                        error_code=None,
                        next_retry_at=None,
                    )

                    file_id = getattr(media, "file_id", None)
                    if not file_id:
                        raise RuntimeError("媒体文件标识缺失，无法下载")
                    decoded_file_id = FileId.decode(str(file_id))
                    with partial.open("ab") as file:
                        chunk_index = start_size // DOWNLOAD_CHUNK_SIZE
                        chunk_total = (
                            (total_size + DOWNLOAD_CHUNK_SIZE - 1)
                            // DOWNLOAD_CHUNK_SIZE
                            if total_size > 0
                            else None
                        )
                        while chunk_total is None or chunk_index < chunk_total:
                            async with self._transfer_gate.slot():
                                chunk = await self._read_telegram_chunk(
                                    client,
                                    decoded_file_id,
                                    total_size,
                                    chunk_index,
                                )
                            if not chunk:
                                raise RuntimeError(
                                    "Telegram 未返回数据块，可能被限流或网络中断"
                                )
                            if len(chunk) > DOWNLOAD_CHUNK_SIZE:
                                raise RuntimeError(
                                    "Telegram 返回的数据块超过预期大小"
                                )
                            file.write(chunk)
                            file.flush()
                            downloaded += len(chunk)
                            chunk_index += 1
                            if total_size > 0 and downloaded > total_size:
                                raise RuntimeError(
                                    "Telegram 返回的数据超过预期文件大小"
                                )
                            update_progress()
                            if len(chunk) < DOWNLOAD_CHUNK_SIZE:
                                break

                    update_progress(force=True)
                    completed_size = file_size(partial)
                    if total_size > 0 and completed_size != total_size:
                        raise RuntimeError(
                            f"下载不完整：已接收 {completed_size} / {total_size} 字节"
                        )
                    if completed_size <= 0:
                        raise RuntimeError("Telegram 未返回下载文件")
                    partial.replace(destination)
                    self.database.update_download(
                        record_id,
                        status="completed",
                        downloaded_size=completed_size,
                        file_name=str(destination),
                        attempt_count=attempt,
                        error=None,
                        error_code=None,
                        next_retry_at=None,
                    )
                    self._record_terminal_success()
                    return True
                except asyncio.CancelledError:
                    self.database.update_download(
                        record_id,
                        status="retry_wait",
                        downloaded_size=file_size(partial),
                        attempt_count=attempt,
                        error="下载已停止，临时文件已保留，可继续下载",
                        error_code="Cancelled",
                        next_retry_at=None,
                    )
                    raise
                except Exception as exc:
                    downloaded = file_size(partial)
                    update_progress(force=True)
                    error_text = str(exc) or exc.__class__.__name__
                    self.last_error = f"{chat_title}: {message.id}: {error_text}"
                    retryable = not isinstance(exc, PermanentDownloadError)
                    if retryable and attempt < max_attempts:
                        delay_index = min(
                            attempt - 1, len(DOWNLOAD_RETRY_DELAYS) - 1
                        )
                        delay = max(0.0, float(DOWNLOAD_RETRY_DELAYS[delay_index]))
                        retry_at = datetime.now(UTC) + timedelta(seconds=delay)
                        self.database.update_download(
                            record_id,
                            status="retry_wait",
                            downloaded_size=downloaded,
                            attempt_count=attempt,
                            error=(
                                f"{error_text}（第 {attempt}/{max_attempts} 次失败，"
                                f"{delay:g} 秒后重试）"
                            ),
                            error_code=exc.__class__.__name__,
                            next_retry_at=retry_at.isoformat(),
                        )
                        active.update(
                            {
                                "downloaded": downloaded,
                                "status": "retrying",
                                "attempt": attempt,
                                "retry_at": time.monotonic() + delay,
                                "retry_seconds": delay,
                                "error": error_text,
                                "speed": 0.0,
                                "last_sample_at": time.monotonic(),
                                "last_sample_bytes": downloaded,
                            }
                        )
                        logger.warning(
                            "Download attempt %s/%s failed for message %s: %s; retrying in %ss",
                            attempt,
                            max_attempts,
                            message.id,
                            error_text,
                            delay,
                        )
                        await self._sleep_with_control(delay, respect_control)
                        continue

                    final_error = (
                        f"{error_text}（已尝试 {attempt} 次，临时文件已保留）"
                    )
                    self.database.update_download(
                        record_id,
                        status="failed",
                        downloaded_size=downloaded,
                        attempt_count=attempt,
                        error=final_error,
                        error_code=exc.__class__.__name__,
                        next_retry_at=None,
                    )
                    self.last_error = f"{chat_title}: {message.id}: {final_error}"
                    self._record_terminal_failure(self.last_error)
                    return False
            return False
        finally:
            async with self._active_lock:
                self._active.pop(record_id, None)
