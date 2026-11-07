"""Pydantic request models and configuration validation."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


MEDIA_TYPES = ("audio", "document", "photo", "video", "voice", "video_note", "animation")
FOLDER_PARTS = ("chat_title", "media_type", "year", "month", "date")
FILENAME_PLACEHOLDERS = ("{message_id}", "{file_name}", "{media_type}", "{date}")
YEAR_MONTH_RE = r"^\d{4}-\d{2}$"


class ProxySetupRequest(BaseModel):
    proxy_url: str | None = Field(default=None, max_length=1024)

class AdminSetupRequest(BaseModel):
    password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError("两次输入的密码不一致")
        return self


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class SettingsUpdate(BaseModel):
    api_id: int | None = Field(default=None, gt=0)
    api_hash: str | None = Field(default=None, max_length=256)
    bot_token: str | None = Field(default=None, max_length=512)
    download_dir: str | None = Field(default=None, max_length=1024)
    media_types: list[str] | None = None
    file_formats: dict[str, list[str]] | None = None
    folder_parts: list[str] | None = None
    filename_template: str | None = Field(default=None, max_length=256)
    max_concurrent_downloads: int | None = Field(default=None, ge=1, le=10)
    proxy_url: str | None = Field(default=None, max_length=1024)

    @field_validator("media_types")
    @classmethod
    def validate_media_types(cls, value: list[str] | None):
        if value is None:
            return value
        cleaned = list(dict.fromkeys(value))
        if not cleaned:
            raise ValueError("至少选择一种媒体类型")
        invalid = sorted(set(cleaned) - set(MEDIA_TYPES))
        if invalid:
            raise ValueError(f"不支持的媒体类型: {', '.join(invalid)}")
        return cleaned

    @field_validator("folder_parts")
    @classmethod
    def validate_folder_parts(cls, value: list[str] | None):
        if value is None:
            return value
        cleaned = list(dict.fromkeys(value))
        invalid = sorted(set(cleaned) - set(FOLDER_PARTS))
        if invalid:
            raise ValueError(f"不支持的目录模板字段: {', '.join(invalid)}")
        return cleaned

    @field_validator("file_formats")
    @classmethod
    def validate_file_formats(cls, value: dict[str, list[str]] | None):
        if value is None:
            return value
        allowed = {"audio", "document", "video", "animation"}
        if set(value) - allowed:
            raise ValueError("文件格式只能配置 audio/document/video/animation")
        cleaned: dict[str, list[str]] = {}
        for key, formats in value.items():
            normalized = [item.strip().lower().lstrip(".") for item in formats if item.strip()]
            cleaned[key] = list(dict.fromkeys(normalized)) or ["all"]
        return cleaned

    @field_validator("filename_template")
    @classmethod
    def validate_filename_template(cls, value: str | None):
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("文件名模板不能为空")
        if "/" in value or "\\" in value:
            raise ValueError("文件名模板不能包含路径分隔符")
        import re

        braces = set(re.findall(r"\{[^{}]+\}", value))
        invalid = braces - set(FILENAME_PLACEHOLDERS)
        if invalid:
            raise ValueError(f"不支持的文件名占位符: {', '.join(sorted(invalid))}")
        return value


class TelegramLoginStartRequest(BaseModel):
    phone_number: str = Field(min_length=5, max_length=32)

    @field_validator("phone_number")
    @classmethod
    def normalize_phone(cls, value: str):
        value = value.strip().replace(" ", "").replace("-", "")
        if not value.startswith("+") or not value[1:].isdigit():
            raise ValueError("手机号必须使用国际格式，例如 +8613800000000")
        return value


class TelegramCodeRequest(BaseModel):
    login_id: str = Field(min_length=8, max_length=128)
    code: str = Field(min_length=1, max_length=16)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str):
        value = value.strip().replace(" ", "")
        if not value.isdigit():
            raise ValueError("验证码只能是数字")
        return value


class TelegramPasswordRequest(BaseModel):
    login_id: str = Field(min_length=8, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class ChatCreateRequest(BaseModel):
    chat_ref: str = Field(min_length=1, max_length=256)
    title: str = Field(default="", max_length=256)
    date_from: date | None = None
    date_to: date | None = None

    @field_validator("chat_ref", "title")
    @classmethod
    def strip_text(cls, value: str):
        return value.strip()

    @model_validator(mode="after")
    def validate_dates(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("开始日期不能晚于结束日期")
        return self


class ChatBulkCreateRequest(BaseModel):
    chats: list[ChatCreateRequest] = Field(min_length=1, max_length=200)


class ChatUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    enabled: bool | None = None
    date_from: date | None = None
    date_to: date | None = None


class ChatImportItem(BaseModel):
    chat_id: int | str
    last_read_message_id: int = 0
    download_filter: str = ""


class AppSettingsResponse(BaseModel):
    api_id: int | None = None
    api_hash_configured: bool = False
    bot_token_configured: bool = False
    download_dir: str
    media_types: list[str]
    file_formats: dict[str, list[str]]
    folder_parts: list[str]
    filename_template: str
    max_concurrent_downloads: int
    proxy_configured: bool = False
    telegram_connected: bool = False


def parse_date_bound(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    parsed = date.fromisoformat(value)
    if end_of_day:
        return datetime.combine(parsed, datetime.max.time(), tzinfo=UTC)
    return datetime.combine(parsed, datetime.min.time(), tzinfo=UTC)
