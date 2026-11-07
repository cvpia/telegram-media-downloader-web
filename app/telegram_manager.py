"""Telegram user-session login and client lifecycle management."""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from pyrogram import Client, raw, utils
from pyrogram.errors import (
    BadMsgNotification,
    FloodWait,
    PasswordHashInvalid,
    PhoneCodeExpired,
    PhoneCodeInvalid,
    SessionPasswordNeeded,
)

from app.settings_service import SettingsService


logger = logging.getLogger(__name__)


@dataclass
class PendingLogin:
    login_id: str
    client: Client
    phone_number: str
    phone_code_hash: str
    needs_password: bool = False


class TelegramManager:
    def __init__(self, settings: SettingsService, session_dir: Path):
        self.settings = settings
        self.session_dir = session_dir
        self._pending: dict[str, PendingLogin] = {}
        self._client: Client | None = None
        self._lock = asyncio.Lock()

    @property
    def session_path(self) -> Path:
        return self.session_dir / "downloader.session"

    def has_session_file(self) -> bool:
        return self.session_path.exists()

    @staticmethod
    def parse_proxy(proxy_url: str) -> dict[str, Any] | None:
        if not proxy_url:
            return None
        parsed = urlparse(proxy_url if "://" in proxy_url else f"http://{proxy_url}")
        if parsed.scheme not in {"socks4", "socks5", "http", "https"}:
            raise ValueError("代理协议必须是 socks4、socks5、http 或 https")
        if not parsed.hostname or not parsed.port:
            raise ValueError("代理地址必须包含主机和端口")
        proxy: dict[str, Any] = {
            "scheme": parsed.scheme,
            "hostname": parsed.hostname,
            "port": parsed.port,
        }
        if parsed.username:
            proxy["username"] = unquote(parsed.username)
        if parsed.password:
            proxy["password"] = unquote(parsed.password)
        return proxy


    def _new_client(self, name: str, api_id: int, api_hash: str) -> Client:
        return Client(
            name,
            api_id=api_id,
            api_hash=api_hash,
            workdir=str(self.session_dir),
            no_updates=True,
            proxy=self.parse_proxy(self.settings.get_proxy_url()),
            sleep_threshold=10,
        )

    @staticmethod
    def is_clock_sync_error(exception: BadMsgNotification) -> bool:
        message = str(exception)
        return any(code in message for code in ("[16]", "[17]", "[20]"))

    async def _send_login_code(
        self,
        client_name: str,
        phone_number: str,
        api_id: int,
        api_hash: str,
    ):
        last_error: BadMsgNotification | None = None
        for attempt in range(2):
            client = self._new_client(client_name, api_id, api_hash)
            await client.connect()
            try:
                return client, await client.send_code(phone_number)
            except BadMsgNotification as exc:
                last_error = exc
                try:
                    await client.disconnect()
                except Exception:
                    pass
                if attempt == 0 and self.is_clock_sync_error(exc):
                    logger.warning("Telegram clock sync mismatch; retrying send_code once")
                    await asyncio.sleep(0.5)
                    continue
                break
            except Exception:
                try:
                    await client.disconnect()
                except Exception:
                    pass
                raise
        raise ValueError(
            "Telegram 客户端与服务器时间不同步。请同步 Windows 系统时间后重试"
        ) from last_error
    async def start_login(self, phone_number: str) -> dict[str, Any]:
        api_id, api_hash = self.settings.get_loader_credentials()
        if not api_id or not api_hash:
            raise ValueError("请先保存 Telegram API ID 和 API Hash")

        await self._discard_pending()

        login_id = secrets.token_urlsafe(18)
        client, sent_code = await self._send_login_code(
            f"login_{login_id}", phone_number, api_id, api_hash
        )
        record = PendingLogin(
            login_id=login_id,
            client=client,
            phone_number=phone_number,
            phone_code_hash=sent_code.phone_code_hash,
        )
        async with self._lock:
            self._pending[login_id] = record
        return {
            "login_id": login_id,
            "next_step": "code",
            "code_type": str(getattr(sent_code, "type", "telegram")),
        }

    async def submit_code(self, login_id: str, code: str) -> dict[str, Any]:
        record = await self._get_pending(login_id)
        try:
            await record.client.sign_in(
                record.phone_number,
                record.phone_code_hash,
                code,
            )
        except SessionPasswordNeeded:
            record.needs_password = True
            return {"next_step": "password"}
        except PhoneCodeInvalid as exc:
            raise ValueError("验证码错误，请重新输入") from exc
        except PhoneCodeExpired as exc:
            raise ValueError("验证码已过期，请重新发送") from exc
        except FloodWait as exc:
            raise ValueError(f"操作过于频繁，请等待 {exc.value} 秒") from exc
        except BadMsgNotification as exc:
            if self.is_clock_sync_error(exc):
                raise ValueError(
                    "Telegram 客户端与服务器时间不同步，请同步 Windows 系统时间后重试"
                ) from exc
            raise

        return await self._finish_login(record)

    async def submit_password(self, login_id: str, password: str) -> dict[str, Any]:
        record = await self._get_pending(login_id)
        if not record.needs_password:
            raise ValueError("当前登录不需要两步验证密码")
        try:
            await record.client.check_password(password)
        except PasswordHashInvalid as exc:
            raise ValueError("两步验证密码错误") from exc
        except BadMsgNotification as exc:
            if self.is_clock_sync_error(exc):
                raise ValueError(
                    "Telegram 客户端与服务器时间不同步，请同步 Windows 系统时间后重试"
                ) from exc
            raise
        return await self._finish_login(record)

    async def _finish_login(self, record: PendingLogin) -> dict[str, Any]:
        me = await record.client.get_me()
        await record.client.disconnect()

        source = self.session_dir / f"login_{record.login_id}.session"
        if not source.exists():
            raise RuntimeError("Telegram 会话文件未生成")
        for stale in (
            self.session_path,
            self.session_path.with_suffix(".session-journal"),
        ):
            if stale.exists():
                stale.unlink()
        source.replace(self.session_path)
        async with self._lock:
            self._pending.pop(record.login_id, None)
        self.settings.set_telegram_connected(True)
        return {
            "next_step": "complete",
            "user": {
                "id": me.id,
                "username": me.username or "",
                "first_name": me.first_name or "",
                "last_name": me.last_name or "",
            },
        }

    async def _get_pending(self, login_id: str) -> PendingLogin:
        async with self._lock:
            record = self._pending.get(login_id)
        if record is None:
            raise ValueError("登录会话不存在或已过期，请重新发送验证码")
        return record

    async def _discard_pending(self) -> None:
        async with self._lock:
            records = list(self._pending.values())
            self._pending.clear()
        for record in records:
            try:
                await record.client.disconnect()
            except Exception:
                pass
            session_file = self.session_dir / f"login_{record.login_id}.session"
            if session_file.exists():
                session_file.unlink()

    async def get_client(self) -> Client:
        async with self._lock:
            if self._client is not None and self._client.is_connected:
                return self._client
            api_id, api_hash = self.settings.get_loader_credentials()
            if not api_id or not api_hash:
                raise ValueError("Telegram API 凭据尚未配置")
            if not self.session_path.exists():
                raise ValueError("Telegram 尚未登录，请先完成登录向导")
            client = self._new_client("downloader", api_id, api_hash)
            await client.connect()
            try:
                await client.get_me()
            except Exception:
                await client.disconnect()
                raise ValueError("Telegram 会话已失效，请重新登录")
            self._client = client
            return client

    @staticmethod
    def _dialog_info(peer: Any, users: dict, chats: dict) -> dict[str, Any] | None:
        def avatar_fields(photo: Any, peer_id: str) -> dict[str, Any]:
            photo_id = getattr(photo, "photo_id", None)
            return {
                "has_photo": photo_id is not None,
                "avatar_url": (
                    f"/api/telegram/dialogs/{peer_id}/avatar?v={photo_id}"
                    if photo_id is not None
                    else ""
                ),
            }

        if isinstance(peer, raw.types.PeerUser):
            user = users.get(peer.user_id)
            if user is None:
                return None
            title = " ".join(
                part for part in (user.first_name, user.last_name) if part
            ) or user.username or f"User {user.id}"
            peer_id = str(utils.get_peer_id(peer))
            return {
                "id": peer_id,
                "title": title,
                "type": "bot" if getattr(user, "bot", False) else "private",
                "username": user.username or "",
                **avatar_fields(getattr(user, "photo", None), peer_id),
            }
        if isinstance(peer, raw.types.PeerChat):
            chat = chats.get(peer.chat_id)
            if chat is None:
                return None
            peer_id = str(utils.get_peer_id(peer))
            return {
                "id": peer_id,
                "title": chat.title or f"Chat {chat.id}",
                "type": "group",
                "username": "",
                **avatar_fields(getattr(chat, "photo", None), peer_id),
            }
        if isinstance(peer, raw.types.PeerChannel):
            chat = chats.get(peer.channel_id)
            if chat is None:
                return None
            peer_id = str(utils.get_peer_id(peer))
            return {
                "id": peer_id,
                "title": chat.title or f"Channel {chat.id}",
                "type": "supergroup" if getattr(chat, "megagroup", False) else "channel",
                "username": getattr(chat, "username", "") or "",
                **avatar_fields(getattr(chat, "photo", None), peer_id),
            }
        return None

    @staticmethod
    def _input_peer(peer: Any, users: dict, chats: dict) -> Any:
        if isinstance(peer, raw.types.PeerUser):
            user = users.get(peer.user_id)
            if user is not None:
                return raw.types.InputPeerUser(
                    user_id=user.id, access_hash=user.access_hash
                )
        if isinstance(peer, raw.types.PeerChat):
            chat = chats.get(peer.chat_id)
            if chat is not None:
                return raw.types.InputPeerChat(chat_id=chat.id)
        if isinstance(peer, raw.types.PeerChannel):
            chat = chats.get(peer.channel_id)
            if chat is not None:
                return raw.types.InputPeerChannel(
                    channel_id=chat.id, access_hash=chat.access_hash
                )
        return raw.types.InputPeerEmpty()

    async def list_dialogs(self, limit: int = 300) -> list[dict[str, Any]]:
        client = await self.get_client()
        dialogs: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        offset_date = 0
        offset_id = 0
        offset_peer = raw.types.InputPeerEmpty()

        while len(dialogs) < limit:
            page_size = min(100, limit - len(dialogs))
            response = await client.invoke(
                raw.functions.messages.GetDialogs(
                    offset_date=offset_date,
                    offset_id=offset_id,
                    offset_peer=offset_peer,
                    limit=page_size,
                    hash=0,
                ),
                sleep_threshold=30,
            )
            users = {user.id: user for user in response.users}
            chats = {chat.id: chat for chat in response.chats}
            messages = {message.id: message for message in response.messages}
            page_dialogs = 0
            new_dialogs = 0
            for dialog in response.dialogs:
                if not isinstance(dialog, raw.types.Dialog):
                    continue
                page_dialogs += 1
                item = self._dialog_info(dialog.peer, users, chats)
                if item is not None and item["id"] not in seen_ids:
                    seen_ids.add(item["id"])
                    dialogs.append(item)
                    new_dialogs += 1
            if page_dialogs < page_size or new_dialogs == 0:
                break
            last_dialog = next(
                (
                    dialog
                    for dialog in reversed(response.dialogs)
                    if isinstance(dialog, raw.types.Dialog)
                ),
                None,
            )
            if last_dialog is None or not last_dialog.top_message:
                break
            offset_id = int(last_dialog.top_message)
            last_message = messages.get(offset_id)
            last_message_date = (
                getattr(last_message, "date", None) if last_message is not None else None
            )
            if isinstance(last_message_date, int):
                offset_date = last_message_date
            elif last_message_date is not None:
                offset_date = utils.datetime_to_timestamp(last_message_date)
            else:
                offset_date = 0
            offset_peer = self._input_peer(last_dialog.peer, users, chats)

        dialogs.sort(
            key=lambda item: (
                item["type"] not in {"channel", "supergroup", "group"},
                item["title"].lower(),
            )
        )
        return dialogs

    async def get_dialog_avatar(self, dialog_id: str) -> tuple[bytes, str] | None:
        client = await self.get_client()
        chat = await client.get_chat(dialog_id, force_full=False)
        photo = getattr(chat, "photo", None)
        if photo is None:
            return None
        file_id = getattr(photo, "small_file_id", None) or getattr(
            photo, "big_file_id", None
        )
        if not file_id:
            return None
        downloaded = await client.download_media(file_id, in_memory=True)
        if downloaded is None:
            return None
        try:
            data = (
                downloaded.getvalue()
                if hasattr(downloaded, "getvalue")
                else downloaded.read()
            )
            name = (getattr(downloaded, "name", "") or "").lower()
        finally:
            close = getattr(downloaded, "close", None)
            if close is not None:
                close()
        if not data:
            return None
        media_type = "image/png" if name.endswith(".png") else "image/jpeg"
        return bytes(data), media_type

    @staticmethod
    def _message_info(message: Any) -> dict[str, Any]:
        user = getattr(message, "from_user", None)
        sender_chat = getattr(message, "sender_chat", None)
        sender = ""
        if user is not None:
            sender = " ".join(
                part
                for part in (
                    getattr(user, "first_name", ""),
                    getattr(user, "last_name", ""),
                )
                if part
            ) or getattr(user, "username", "") or str(getattr(user, "id", ""))
        elif sender_chat is not None:
            sender = getattr(sender_chat, "title", "") or ""

        media = getattr(message, "media", None)
        media_type = getattr(media, "value", "") if media is not None else ""
        media_labels = {
            "photo": "图片",
            "video": "视频",
            "document": "文件",
            "audio": "音频",
            "voice": "语音",
            "video_note": "视频消息",
            "animation": "GIF / 动画",
            "sticker": "贴纸",
        }
        text = getattr(message, "text", "") or getattr(message, "caption", "") or ""
        media_name = ""
        if media_type and media_type != "photo":
            content = getattr(message, media_type, None)
            media_name = getattr(content, "file_name", "") or ""
        if not text:
            text = f"[{media_labels.get(media_type, '媒体' if media_type else '消息')}]"

        date = getattr(message, "date", None)
        if isinstance(date, datetime):
            message_date = date.isoformat()
        elif isinstance(date, int):
            message_date = utils.timestamp_to_datetime(date).isoformat()
        else:
            message_date = ""

        return {
            "id": int(getattr(message, "id", 0)),
            "date": message_date,
            "sender": sender,
            "text": text,
            "media_type": media_type,
            "media_label": media_labels.get(media_type, ""),
            "media_name": media_name,
        }

    async def list_dialog_messages(
        self, dialog_id: str, limit: int = 30
    ) -> list[dict[str, Any]]:
        client = await self.get_client()
        message_limit = max(1, min(int(limit), 100))
        messages: list[dict[str, Any]] = []
        async for message in client.get_chat_history(dialog_id, limit=message_limit):
            messages.append(self._message_info(message))
        messages.reverse()
        return messages

    async def logout(self) -> None:
        async with self._lock:
            client = self._client
            self._client = None
        if client is not None:
            try:
                if client.is_connected:
                    await client.log_out()
            except Exception:
                pass
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass
        for path in self.session_dir.glob("downloader.session*"):
            if path.exists():
                path.unlink()
        self.settings.set_telegram_connected(False)

    async def close(self) -> None:
        await self._discard_pending()
        async with self._lock:
            client = self._client
            self._client = None
        if client is not None and client.is_connected:
            await client.disconnect()