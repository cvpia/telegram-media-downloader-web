"""Typed access to persisted application settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.database import Database
from app.schemas import SettingsUpdate
from app.security import SecretCipher


DEFAULT_MEDIA_TYPES = ["audio", "document", "photo", "video", "voice", "video_note"]
DEFAULT_FILE_FORMATS = {
    "audio": ["all"],
    "document": ["all"],
    "video": ["all"],
    "animation": ["all"],
}
DEFAULT_FOLDER_PARTS = ["chat_title", "year", "month", "media_type"]
DEFAULT_FILENAME_TEMPLATE = "{message_id} - {file_name}"
ENCRYPTED_KEYS = {"api_hash", "proxy_url"}


class SettingsService:
    def __init__(
        self,
        database: Database,
        cipher: SecretCipher,
        default_download_dir: Path,
    ):
        self.database = database
        self.cipher = cipher
        self.default_download_dir = default_download_dir

    def _get(self, key: str, default: Any = None) -> Any:
        value = self.database.get_setting(key, default)
        if key in ENCRYPTED_KEYS and isinstance(value, str) and value:
            return self.cipher.decrypt(value)
        return value

    def _set(self, key: str, value: Any) -> None:
        if key in ENCRYPTED_KEYS and isinstance(value, str) and value:
            value = self.cipher.encrypt(value)
        self.database.set_setting(key, value)

    @property
    def admin_configured(self) -> bool:
        return bool(self.database.get_setting("admin_password_hash"))

    @property
    def proxy_step_completed(self) -> bool:
        return bool(self.database.get_setting("setup_proxy_completed", False))

    def mark_proxy_step_completed(self) -> None:
        self.database.set_setting("setup_proxy_completed", True)

    @property
    def api_configured(self) -> bool:
        return bool(self._get("api_id")) and bool(self._get("api_hash"))

    @property
    def telegram_connected(self) -> bool:
        return bool(self.database.get_setting("telegram_logged_in", False))

    @property
    def ready(self) -> bool:
        return (
            self.admin_configured
            and self.api_configured
            and self.telegram_connected
            and bool(self.database.list_chats())
        )

    def set_admin_password_hash(self, password_hash: str) -> None:
        self.database.set_setting("admin_password_hash", password_hash)

    def get_admin_password_hash(self) -> str:
        return self.database.get_setting("admin_password_hash", "")

    def get_loader_credentials(self) -> tuple[int | None, str]:
        api_id = self._get("api_id")
        api_hash = self._get("api_hash", "")
        return (int(api_id) if api_id else None, str(api_hash or ""))

    def get_proxy_url(self) -> str:
        return str(self._get("proxy_url", "") or "")

    def set_telegram_connected(self, connected: bool) -> None:
        self.database.set_setting("telegram_logged_in", connected)

    def download_dir(self) -> Path:
        raw = self._get("download_dir")
        return Path(raw).expanduser().resolve() if raw else self.default_download_dir

    def folder_parts(self) -> list[str]:
        return list(self._get("folder_parts", DEFAULT_FOLDER_PARTS))

    def media_types(self) -> list[str]:
        return list(self._get("media_types", DEFAULT_MEDIA_TYPES))

    def file_formats(self) -> dict[str, list[str]]:
        value = self._get("file_formats", DEFAULT_FILE_FORMATS)
        return {str(key): list(formats) for key, formats in value.items()}

    def filename_template(self) -> str:
        return str(self._get("filename_template", DEFAULT_FILENAME_TEMPLATE))

    def max_concurrent_downloads(self) -> int:
        return int(self._get("max_concurrent_downloads", 1))

    def public(self) -> dict[str, Any]:
        api_id, api_hash = self.get_loader_credentials()
        return {
            "api_id": api_id,
            "api_hash_configured": bool(api_hash),
            "download_dir": str(self.download_dir()),
            "media_types": self.media_types(),
            "file_formats": self.file_formats(),
            "folder_parts": self.folder_parts(),
            "filename_template": self.filename_template(),
            "max_concurrent_downloads": self.max_concurrent_downloads(),
            "proxy_configured": bool(self.get_proxy_url()),
            "telegram_connected": self.telegram_connected,
        }

    def update(self, payload: SettingsUpdate) -> dict[str, Any]:
        values = payload.model_dump(exclude_unset=True)

        if "api_id" in values and values["api_id"] is not None:
            self._set("api_id", int(values["api_id"]))
        if "api_hash" in values and values["api_hash"]:
            self._set("api_hash", values["api_hash"].strip())
        if "download_dir" in values and values["download_dir"] is not None:
            download_dir = Path(values["download_dir"]).expanduser().resolve()
            download_dir.mkdir(parents=True, exist_ok=True)
            self._set("download_dir", str(download_dir))
        if "media_types" in values and values["media_types"] is not None:
            self._set("media_types", list(values["media_types"]))
        if "file_formats" in values and values["file_formats"] is not None:
            self._set("file_formats", values["file_formats"])
        if "folder_parts" in values and values["folder_parts"] is not None:
            self._set("folder_parts", list(values["folder_parts"]))
        if "filename_template" in values and values["filename_template"]:
            self._set("filename_template", values["filename_template"])
        if (
            "max_concurrent_downloads" in values
            and values["max_concurrent_downloads"] is not None
        ):
            self._set("max_concurrent_downloads", int(values["max_concurrent_downloads"]))
        if "proxy_url" in values:
            proxy_url = str(values["proxy_url"] or "").strip()
            self._set("proxy_url", proxy_url)

        return self.public()

    def export_yaml(self) -> str:
        import yaml

        api_id, api_hash = self.get_loader_credentials()
        data = {
            "api_id": api_id,
            "api_hash": api_hash,
            "chat": [
                {
                    "chat_id": chat["chat_ref"],
                    "title": chat["title"],
                    "last_read_message_id": chat["last_message_id"],
                    "date_from": chat.get("date_from"),
                    "date_to": chat.get("date_to"),
                }
                for chat in self.database.list_chats()
            ],
            "media_types": self.media_types(),
            "file_formats": self.file_formats(),
            "save_path": str(self.download_dir()),
            "file_path_prefix": self.folder_parts(),
            "file_name_prefix": self.filename_template(),
            "max_download_task": self.max_concurrent_downloads(),
            "proxy_url": self.get_proxy_url(),
        }
        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)

    def import_yaml(self, content: str) -> int:
        import yaml

        data = yaml.safe_load(content) or {}
        if not isinstance(data, dict):
            raise ValueError("YAML 根节点必须是对象")
        update_data: dict[str, Any] = {}
        if data.get("api_id"):
            update_data["api_id"] = int(data["api_id"])
        if data.get("api_hash"):
            update_data["api_hash"] = str(data["api_hash"])
        if data.get("media_types"):
            update_data["media_types"] = list(data["media_types"])
        if data.get("file_formats"):
            update_data["file_formats"] = data["file_formats"]
        if data.get("save_path"):
            update_data["download_dir"] = str(data["save_path"])
        if data.get("file_path_prefix"):
            update_data["folder_parts"] = list(data["file_path_prefix"])
        if isinstance(data.get("file_name_prefix"), str):
            update_data["filename_template"] = data["file_name_prefix"]
        if data.get("max_download_task"):
            update_data["max_concurrent_downloads"] = int(data["max_download_task"])
        if data.get("proxy_url"):
            update_data["proxy_url"] = str(data["proxy_url"])
        elif isinstance(data.get("proxy"), dict):
            proxy = data["proxy"]
            scheme = proxy.get("scheme", "http")
            hostname = proxy.get("hostname", "")
            port = proxy.get("port", "")
            if hostname and port:
                update_data["proxy_url"] = f"{scheme}://{hostname}:{port}"

        self.update(SettingsUpdate(**update_data))

        count = 0
        for item in data.get("chat") or []:
            if isinstance(item, dict) and item.get("chat_id") is not None:
                chat = self.database.upsert_chat(
                    str(item["chat_id"]),
                    str(item.get("title", "")),
                    item.get("date_from"),
                    item.get("date_to"),
                )
                last_message_id = int(item.get("last_read_message_id", 0) or 0)
                if last_message_id:
                    self.database.set_last_message(chat["id"], last_message_id)
                count += 1
        return count