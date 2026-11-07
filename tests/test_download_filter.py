from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.database import Database
from app.download_filter import (
    FilterValidationError,
    build_filter_context,
    compile_download_filter,
    validate_download_filter,
)


def media_context(**overrides):
    base = {
        "message_date": datetime(2025, 6, 15, 12, 30, tzinfo=UTC),
        "message_id": 2048,
        "message_caption": "电影 4K 高清",
        "media_file_size": 50 * 1024 * 1024,
        "media_width": 1920,
        "media_height": 1080,
        "media_file_name": "movie.1080p.mp4",
        "media_duration": 7200,
        "media_type": "video",
        "file_extension": "mp4",
    }
    base.update(overrides)
    return base


def test_media_type_regex_filter():
    rule = compile_download_filter("media_type == r'(video|audio)'")
    assert rule.matches(media_context())
    assert rule.matches(media_context(media_type="audio"))
    assert not rule.matches(media_context(media_type="photo"))


def test_size_units_arithmetic_and_boolean_filter():
    rule = compile_download_filter(
        "file_size >= 10MB and file_size < 100 * 1024 * 1024"
    )
    assert rule.matches(media_context())
    assert not rule.matches(media_context(media_file_size=5 * 1024 * 1024))


def test_caption_or_file_name_filter_with_parentheses():
    rule = compile_download_filter(
        "file_extension == r'(mp4|mkv)' and "
        "(caption == r'.*电影.*' or file_name == r'.*1080p.*')"
    )
    assert rule.matches(media_context())
    assert rule.matches(media_context(message_caption="", media_file_name="movie.1080p.mkv"))
    assert not rule.matches(media_context(message_caption="", media_file_name="movie.avi"))


def test_date_aliases_accept_multiple_formats():
    assert compile_download_filter("message_date >= 2025.06.01").matches(media_context())
    assert compile_download_filter(
        "message_date_time < 2025-07-01 00:00:00"
    ).matches(media_context())
    assert not compile_download_filter("message_date >= 2025/07/01").matches(
        media_context()
    )


def test_not_operator_and_extension_inequality():
    rule = compile_download_filter(
        "not (file_extension == r'(avi|wmv)') and media_duration >= 60"
    )
    assert rule.matches(media_context())
    assert not rule.matches(media_context(media_duration=30))


def test_empty_filter_matches_everything():
    validate_download_filter("")
    assert compile_download_filter("").matches({})


@pytest.mark.parametrize(
    "expression, message",
    [
        ("unknown_field == 'x'", "不支持的过滤字段"),
        ("file_size == 'x'", "需要数字值"),
        ("caption > 'x'", "只支持"),
        ("caption == r'['", "无效的正则"),
        ("(message_id > 1", "缺少右括号"),
        ("message_id >", "需要数字值"),
    ],
)
def test_invalid_filter_expressions(expression, message):
    with pytest.raises(FilterValidationError, match=message):
        compile_download_filter(expression)


def test_build_filter_context_from_telegram_message():
    message = SimpleNamespace(
        id=99,
        date=datetime(2025, 1, 2, tzinfo=UTC),
        caption="Caption",
        text="ignored",
    )
    media = SimpleNamespace(
        file_size=1234,
        width=1280,
        height=720,
        duration=42,
    )
    context = build_filter_context(message, "video", media, "clip.MP4", ".MP4")
    assert context == {
        "message_date": datetime(2025, 1, 2, tzinfo=UTC),
        "message_id": 99,
        "message_caption": "Caption",
        "media_file_size": 1234,
        "media_width": 1280,
        "media_height": 720,
        "media_file_name": "clip.MP4",
        "media_duration": 42,
        "media_type": "video",
        "file_extension": "mp4",
    }


def test_database_migrates_existing_chats_table_with_download_filter(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_ref TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                last_message_id INTEGER NOT NULL DEFAULT 0,
                date_from TEXT,
                date_to TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    database = Database(path)
    database.initialize()
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(chats)")}
    assert "download_filter" in columns

    chat = database.upsert_chat("@legacy", "Legacy", download_filter="file_size >= 1MB")
    assert chat["download_filter"] == "file_size >= 1MB"
