"""Telegram bot commands and message forwarding monitor."""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
from types import SimpleNamespace
from typing import Any

from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler
from pyrogram.types import BotCommand

from app.database import Database
from app.download_filter import build_filter_context, compile_download_filter
from app.downloader import DownloaderService, detect_media, media_extension
from app.settings_service import SettingsService
from app.telegram_manager import TelegramManager
from app.version import APP_NAME, APP_VERSION


logger = logging.getLogger(__name__)
NUMERIC_CHAT_RE = re.compile(r"^-?\d+$")
BOT_COMMANDS = [
    BotCommand("start", "查看机器人说明"),
    BotCommand("help", "查看命令帮助"),
    BotCommand("download", "下载指定消息范围"),
    BotCommand("forward", "转发指定消息范围"),
    BotCommand("listen_forward", "创建消息监听转发规则"),
    BotCommand("stop_listen", "停用监听转发规则"),
]


def normalize_chat_ref(value: str) -> str:
    """Convert common Telegram links to references accepted by Pyrogram."""
    ref = str(value or "").strip()
    if not ref:
        raise ValueError("聊天地址不能为空")
    if ref.startswith(("https://t.me/", "http://t.me/", "t.me/")):
        path = ref.split("t.me/", 1)[1].strip("/")
        parts = [part for part in path.split("/") if part]
        if not parts:
            raise ValueError("无效的 Telegram 链接")
        if parts[0] == "c" and len(parts) >= 2 and parts[1].isdigit():
            return f"-100{parts[1]}"
        if parts[0].startswith("+") or parts[0] == "joinchat":
            raise ValueError("不支持私有邀请链接，请填写已加入会话的用户名或数字 ID")
        return f"@{parts[0].lstrip('@')}"
    if ref.startswith("@"):
        return ref
    if NUMERIC_CHAT_RE.fullmatch(ref):
        return str(int(ref))
    return ref


def chat_ref_value(value: str | int) -> int | str:
    text = str(value)
    return int(text) if NUMERIC_CHAT_RE.fullmatch(text) else text


class BotService:
    def __init__(
        self,
        database: Database,
        settings: SettingsService,
        telegram: TelegramManager,
        downloader: DownloaderService,
        *,
        enabled: bool = True,
        monitor_interval: float = 30.0,
    ):
        self.database = database
        self.settings = settings
        self.telegram = telegram
        self.downloader = downloader
        self.enabled = enabled
        self.monitor_interval = max(5.0, float(monitor_interval))
        self._bot: Client | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._manual_tasks: set[asyncio.Task[Any]] = set()
        self._lifecycle_lock = asyncio.Lock()
        self._check_lock = asyncio.Lock()
        self._owner_id: int | None = None
        self._bot_id: int | None = None
        self._username = ""
        self._error = ""

    def snapshot(self) -> dict[str, Any]:
        running = bool(self._bot is not None and self._bot.is_connected)
        return {
            "configured": bool(self.settings.get_bot_token()),
            "enabled": self.enabled,
            "running": running,
            "username": self._username,
            "owner_id": self._owner_id,
            "error": self._error,
            "monitor_interval": int(self.monitor_interval),
            "rules": len(self.database.list_forward_rules()),
        }

    async def start(self) -> dict[str, Any]:
        async with self._lifecycle_lock:
            if self._bot is not None and self._bot.is_connected:
                return self.snapshot()
            self._error = ""
            if not self.enabled:
                self._error = "后台服务已在测试模式中禁用"
                return self.snapshot()
            token = self.settings.get_bot_token()
            if not token:
                self._error = "尚未配置 Bot Token"
                return self.snapshot()
            if not self.settings.telegram_connected:
                self._error = "请先完成 Telegram 用户账号登录"
                return self.snapshot()

            bot: Client | None = None
            try:
                user_client = await self.telegram.get_client()
                owner = await user_client.get_me()
                self._owner_id = int(owner.id)
                bot = self.telegram.create_bot_client(token)
                self._register_handlers(bot)
                await bot.start()
                bot_info = await bot.get_me()
                self._bot_id = int(bot_info.id)
                self._username = str(getattr(bot_info, "username", "") or "")
                await bot.set_bot_commands(BOT_COMMANDS)
                self._bot = bot
                self._monitor_task = asyncio.create_task(
                    self._monitor_loop(), name="telegram-forward-monitor"
                )
            except Exception as exc:
                self._error = str(exc)
                logger.exception("Unable to start Telegram bot")
                if bot is not None:
                    try:
                        await bot.stop()
                    except Exception:
                        pass
                self._bot = None
            return self.snapshot()

    async def restart(self) -> dict[str, Any]:
        await self.stop()
        return await self.start()

    async def stop(self) -> None:
        monitor_task = self._monitor_task
        self._monitor_task = None
        if monitor_task is not None and not monitor_task.done():
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
        for task in list(self._manual_tasks):
            task.cancel()
        if self._manual_tasks:
            await asyncio.gather(*self._manual_tasks, return_exceptions=True)
            self._manual_tasks.clear()
        bot = self._bot
        self._bot = None
        self._bot_id = None
        self._username = ""
        if bot is not None:
            try:
                await bot.stop()
            except Exception:
                logger.exception("Unable to stop Telegram bot")

    async def close(self) -> None:
        await self.stop()

    def _register_handlers(self, bot: Client) -> None:
        bot.add_handler(
            MessageHandler(
                self._handle_command,
                filters.command(
                    ["start", "help", "download", "forward", "listen_forward", "stop_listen"]
                ),
            )
        )
        bot.add_handler(MessageHandler(self._handle_media, filters.media))

    def _is_owner(self, message: Any) -> bool:
        user_id = getattr(getattr(message, "from_user", None), "id", None)
        return self._owner_id is not None and int(user_id or 0) == self._owner_id

    async def _handle_command(self, bot: Client, message: Any) -> None:
        if not self._is_owner(message):
            return
        text = (getattr(message, "text", None) or "").strip()
        command = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text else ""
        try:
            if command in {"/start", "/help"}:
                await self._send_help(bot, message)
            elif command == "/listen_forward":
                await self._bot_listen_forward(bot, message, text)
            elif command == "/forward":
                await self._bot_forward(bot, message, text, download=False)
            elif command == "/download":
                await self._bot_forward(bot, message, text, download=True)
            elif command == "/stop_listen":
                await self._bot_stop_listen(bot, message, text)
        except Exception as exc:
            logger.exception("Bot command failed")
            await bot.send_message(message.chat.id, f"操作失败：{exc}")

    async def _handle_media(self, bot: Client, message: Any) -> None:
        if not self._is_owner(message):
            return
        try:
            result = await self.downloader.start_bot_download(
                message, bot, chat_title="Bot 收件箱"
            )
            await bot.send_message(
                message.chat.id,
                f"已加入下载队列：{result['file_name']}",
            )
        except Exception as exc:
            await bot.send_message(message.chat.id, f"保存失败：{exc}")

    async def _send_help(self, bot: Client, message: Any) -> None:
        text = (
            f"{APP_NAME} v{APP_VERSION}\n\n"
            "直接把图片、视频或文件发送给机器人，会保存到服务器下载目录。\n\n"
            "下载消息范围：\n"
            "/download <源会话> <开始ID> <结束ID> [筛选表达式]\n\n"
            "立即转发消息范围：\n"
            "/forward <源会话> <目标会话> <开始ID> <结束ID> [筛选表达式]\n\n"
            "监听并自动转发新消息：\n"
            "/listen_forward <源会话> <目标会话> [筛选表达式]\n\n"
            "停用监听规则：\n"
            "/stop_listen <规则ID|all>\n\n"
            "会话可填写 @username、-100... 数字 ID 或 t.me 频道链接。"
        )
        await bot.send_message(message.chat.id, text)

    async def _bot_listen_forward(self, bot: Client, message: Any, text: str) -> None:
        args = shlex.split(text)
        if len(args) < 3:
            raise ValueError(
                "格式：/listen_forward <源会话> <目标会话> [筛选表达式]"
            )
        rule = await self.create_rule(
            source_chat=args[1],
            target_chat=args[2],
            filter_expression=" ".join(args[3:]),
            start_message_id=None,
        )
        await bot.send_message(
            message.chat.id,
            f"已创建监听规则 #{rule['id']}：\n"
            f"{rule['source_title']} → {rule['target_title']}\n"
            "可在 Web“消息转发”页面管理。",
        )

    async def _bot_stop_listen(self, bot: Client, message: Any, text: str) -> None:
        args = shlex.split(text)
        target = args[1] if len(args) > 1 else "all"
        rules = self.database.list_forward_rules()
        changed = 0
        for rule in rules:
            if target == "all" or str(rule["id"]) == target:
                self.database.update_forward_rule(rule["id"], enabled=False)
                changed += 1
        await bot.send_message(message.chat.id, f"已停用 {changed} 条监听规则")

    async def _bot_forward(
        self, bot: Client, message: Any, text: str, *, download: bool
    ) -> None:
        args = shlex.split(text)
        minimum = 4 if download else 5
        if len(args) < minimum:
            example = (
                "/download <源会话> <开始ID> <结束ID> [筛选]"
                if download
                else "/forward <源会话> <目标会话> <开始ID> <结束ID> [筛选]"
            )
            raise ValueError(f"格式：{example}")
        source_chat = args[1]
        if download:
            target_chat = ""
            start_id, end_id = int(args[2]), int(args[3])
            filter_expression = " ".join(args[4:])
        else:
            target_chat = args[2]
            start_id, end_id = int(args[3]), int(args[4])
            filter_expression = " ".join(args[5:])

        await bot.send_message(message.chat.id, "任务已开始，完成后会在这里通知。")

        async def run() -> None:
            try:
                if download:
                    result = await self.download_range(
                        source_chat, start_id, end_id, filter_expression
                    )
                    text_result = (
                        f"下载任务已排队 {result['queued']} 条，跳过 {result['skipped']} 条"
                    )
                else:
                    result = await self.forward_range(
                        source_chat,
                        target_chat,
                        start_id,
                        end_id,
                        filter_expression,
                    )
                    text_result = (
                        f"转发完成 {result['forwarded']} 条，跳过 {result['skipped']} 条"
                    )
                await bot.send_message(message.chat.id, text_result)
            except Exception as exc:
                logger.exception("Bot range task failed")
                await bot.send_message(message.chat.id, f"任务失败：{exc}")

        task = asyncio.create_task(run(), name=f"bot-range-{getattr(message, 'id', 0)}")
        self._manual_tasks.add(task)
        task.add_done_callback(self._manual_tasks.discard)

    async def create_rule(
        self,
        *,
        source_chat: str,
        target_chat: str,
        filter_expression: str = "",
        start_message_id: int | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        client = await self.telegram.get_client()
        source_ref, source = await self._resolve_chat(client, source_chat)
        target_ref, target = await self._resolve_chat(client, target_chat)
        if int(source.id) == int(target.id):
            raise ValueError("源会话和目标会话不能相同")
        if self._bot_id is not None and int(target.id) == self._bot_id:
            raise ValueError("目标会话不能是 Bot 自己，否则会形成循环转发")
        latest_id = await self._latest_message_id(client, source.id)
        checkpoint = (
            int(start_message_id)
            if start_message_id is not None and int(start_message_id) > 0
            else latest_id
        )
        return self.database.create_forward_rule(
            source_chat=source_ref,
            source_title=self._chat_title(source, source_ref),
            target_chat=target_ref,
            target_title=self._chat_title(target, target_ref),
            filter_expression=filter_expression.strip(),
            enabled=enabled,
            last_message_id=checkpoint,
        )

    async def update_rule(self, rule_id: int, **updates: Any) -> dict[str, Any] | None:
        rule = self.database.get_forward_rule(rule_id)
        if rule is None:
            return None
        client = None
        source_changed = False
        if updates.get("source_chat") is not None:
            client = client or await self.telegram.get_client()
            source_ref, source = await self._resolve_chat(client, updates["source_chat"])
            updates["source_chat"] = source_ref
            updates["source_title"] = self._chat_title(source, source_ref)
            source_changed = source_ref != rule["source_chat"]
        if updates.get("target_chat") is not None:
            client = client or await self.telegram.get_client()
            target_ref, target = await self._resolve_chat(client, updates["target_chat"])
            if self._bot_id is not None and int(target.id) == self._bot_id:
                raise ValueError("目标会话不能是 Bot 自己，否则会形成循环转发")
            updates["target_chat"] = target_ref
            updates["target_title"] = self._chat_title(target, target_ref)
        if str(updates.get("source_chat", rule["source_chat"])) == str(
            updates.get("target_chat", rule["target_chat"])
        ):
            raise ValueError("源会话和目标会话不能相同")
        start_message_id = updates.pop("start_message_id", None)
        if start_message_id is not None and int(start_message_id) > 0:
            updates["last_message_id"] = int(start_message_id)
        elif source_changed:
            assert client is not None
            updates["last_message_id"] = await self._latest_message_id(
                client, chat_ref_value(updates["source_chat"])
            )
        return self.database.update_forward_rule(rule_id, **updates)

    async def _resolve_chat(self, client: Client, value: str) -> tuple[str, Any]:
        ref = normalize_chat_ref(value)
        chat = await client.get_chat(chat_ref_value(ref))
        if chat is None:
            raise ValueError(f"无法读取会话：{value}")
        return str(int(chat.id)), chat

    @staticmethod
    def _chat_title(chat: Any, fallback: str) -> str:
        return str(
            getattr(chat, "title", None)
            or getattr(chat, "username", None)
            or getattr(chat, "first_name", None)
            or fallback
        )

    @staticmethod
    async def _latest_message_id(client: Client, chat_id: int | str) -> int:
        async for message in client.get_chat_history(chat_id, limit=1):
            return int(message.id)
        return 0

    @staticmethod
    async def _collect_after(
        client: Client,
        chat_id: int | str,
        last_message_id: int,
        *,
        max_messages: int = 500,
    ) -> list[Any]:
        messages: list[Any] = []
        offset_id = 0
        while len(messages) < max_messages:
            batch = [
                message
                async for message in client.get_chat_history(
                    chat_id,
                    limit=min(100, max_messages - len(messages)),
                    offset_id=offset_id,
                    min_id=last_message_id,
                )
            ]
            if not batch:
                break
            messages.extend(batch)
            oldest_id = int(batch[-1].id)
            if oldest_id <= last_message_id or oldest_id == offset_id:
                break
            offset_id = oldest_id
        return sorted(messages, key=lambda item: int(item.id))

    @staticmethod
    async def _collect_range(
        client: Client,
        chat_id: int | str,
        start_id: int,
        end_id: int,
        *,
        max_messages: int = 500,
    ) -> list[Any]:
        kwargs: dict[str, Any] = {"limit": max_messages}
        if start_id > 0:
            kwargs["min_id"] = start_id - 1
        if end_id > 0:
            kwargs["max_id"] = end_id + 1
        messages = [
            message async for message in client.get_chat_history(chat_id, **kwargs)
        ]
        return sorted(messages, key=lambda item: int(item.id))

    @staticmethod
    def _message_context(message: Any) -> tuple[dict[str, Any], Any | None]:
        detected = detect_media(message)
        if detected is None:
            return (
                build_filter_context(
                    message,
                    "text",
                    SimpleNamespace(),
                    None,
                    "",
                ),
                None,
            )
        media_type, media = detected
        file_name = getattr(media, "file_name", None)
        extension = media_extension(media_type, media, file_name)
        return (
            build_filter_context(message, media_type, media, file_name, extension),
            (media_type, media),
        )

    async def _copy_message(
        self,
        user_client: Client,
        source_chat: int | str,
        target_chat: int | str,
        message_id: int,
    ) -> None:
        errors: list[Exception] = []
        clients = [user_client, self._bot] if self._bot is not None else [user_client]
        for client in clients:
            if client is None:
                continue
            try:
                await client.copy_message(
                    chat_id=target_chat,
                    from_chat_id=source_chat,
                    message_id=message_id,
                )
                return
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise errors[-1]
        raise RuntimeError("没有可用的 Telegram 客户端")

    async def forward_range(
        self,
        source_chat: str,
        target_chat: str,
        start_id: int,
        end_id: int,
        filter_expression: str = "",
    ) -> dict[str, int]:
        client = await self.telegram.get_client()
        source_ref, source = await self._resolve_chat(client, source_chat)
        target_ref, target = await self._resolve_chat(client, target_chat)
        if self._bot_id is not None and int(target.id) == self._bot_id:
            raise ValueError("目标会话不能是 Bot 自己，否则会形成循环转发")
        rule_filter = compile_download_filter(filter_expression)
        messages = await self._collect_range(client, source.id, start_id, end_id)
        forwarded = 0
        skipped = 0
        for message in messages:
            context, _ = self._message_context(message)
            if not rule_filter.matches(context):
                skipped += 1
                continue
            await self._copy_message(client, source.id, target.id, int(message.id))
            forwarded += 1
        return {"forwarded": forwarded, "skipped": skipped, "checked": len(messages)}

    async def download_range(
        self,
        source_chat: str,
        start_id: int,
        end_id: int,
        filter_expression: str = "",
    ) -> dict[str, int]:
        client = await self.telegram.get_client()
        source_ref, source = await self._resolve_chat(client, source_chat)
        rule_filter = compile_download_filter(filter_expression)
        messages = await self._collect_range(client, source.id, start_id, end_id)
        queued = 0
        skipped = 0
        for message in messages:
            context, media_info = self._message_context(message)
            if media_info is None or not rule_filter.matches(context):
                skipped += 1
                continue
            await self.downloader.start_direct_download(
                str(source.id), int(message.id), self._chat_title(source, source_ref)
            )
            queued += 1
        return {"queued": queued, "skipped": skipped, "checked": len(messages)}

    async def check_rule(self, rule_id: int) -> dict[str, Any]:
        async with self._check_lock:
            rule = self.database.get_forward_rule(rule_id)
            if rule is None:
                raise ValueError("转发规则不存在")
            if not rule["enabled"]:
                return {"rule_id": rule_id, "forwarded": 0, "skipped": 0, "checked": 0}
            client = await self.telegram.get_client()
            rule_filter = compile_download_filter(rule.get("filter_expression"))
            source_chat = chat_ref_value(rule["source_chat"])
            target_chat = chat_ref_value(rule["target_chat"])
            last_processed = int(rule.get("last_message_id") or 0)
            forwarded = 0
            skipped = 0
            try:
                messages = await self._collect_after(
                    client, source_chat, last_processed
                )
                for message in messages:
                    context, _ = self._message_context(message)
                    if not rule_filter.matches(context):
                        skipped += 1
                        last_processed = int(message.id)
                        continue
                    await self._copy_message(
                        client, source_chat, target_chat, int(message.id)
                    )
                    forwarded += 1
                    last_processed = int(message.id)
                self.database.record_forward_result(
                    rule_id,
                    last_message_id=last_processed,
                    forwarded_count=forwarded,
                    error=None,
                )
                return {
                    "rule_id": rule_id,
                    "forwarded": forwarded,
                    "skipped": skipped,
                    "checked": len(messages),
                }
            except Exception as exc:
                self.database.record_forward_result(
                    rule_id,
                    last_message_id=last_processed,
                    forwarded_count=forwarded,
                    error=str(exc),
                )
                raise

    async def check_all_rules(self) -> list[dict[str, Any]]:
        results = []
        for rule in self.database.list_forward_rules(enabled_only=True):
            try:
                results.append(await self.check_rule(int(rule["id"])))
            except Exception:
                logger.exception("Forward rule %s failed", rule["id"])
        return results

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(self.monitor_interval)
            if not self.settings.telegram_connected:
                continue
            try:
                await self.check_all_rules()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Forward monitor cycle failed")
