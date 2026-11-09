from __future__ import annotations

import sqlite3
from pathlib import Path

from app.database import Database


def test_initialize_migrates_download_retry_columns(tmp_path: Path):
    path = tmp_path / "app.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
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
            );
            CREATE TABLE downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                chat_title TEXT NOT NULL DEFAULT '',
                message_id INTEGER NOT NULL,
                file_name TEXT NOT NULL DEFAULT '',
                media_type TEXT NOT NULL DEFAULT '',
                total_size INTEGER NOT NULL DEFAULT 0,
                downloaded_size INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                error TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                UNIQUE(chat_id, message_id),
                FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE
            );
            INSERT INTO chats(
                id, chat_ref, title, enabled, last_message_id, created_at, updated_at
            ) VALUES (1, '@legacy', 'Legacy', 1, 0, '2026-01-01', '2026-01-01');
            INSERT INTO downloads(
                id, chat_id, chat_title, message_id, file_name, media_type,
                total_size, downloaded_size, status, error, started_at
            ) VALUES (
                1, 1, 'Legacy', 10, '/downloads/legacy.mp4', 'video',
                1024, 0, 'failed', 'network', '2026-01-01'
            );
            """
        )

    database = Database(path)
    database.initialize()

    with sqlite3.connect(path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(downloads)")
        }
    assert {"attempt_count", "error_code", "next_retry_at"} <= columns

    record = database.get_download(1)
    assert record is not None
    assert record["status"] == "failed"
    assert record["attempt_count"] == 0
    assert database.download_counts()["failed"] == 1

    assert database.requeue_failed_downloads(10) == 1
    retried = database.get_download(1)
    assert retried is not None
    assert retried["status"] == "retry_wait"
    assert retried["error"] is None
    assert retried["finished_at"] is None


def test_dialog_cache_round_trip(tmp_path: Path):
    database = Database(tmp_path / "app.db")
    database.initialize()

    saved = database.save_dialog_cache(
        [
            {"id": "1", "title": "Cached", "type": "channel"},
            {"id": "", "title": "Invalid", "type": "channel"},
        ]
    )

    assert saved["cached_at"] > 0
    assert database.get_dialog_cache()["dialogs"] == [
        {"id": "1", "title": "Cached", "type": "channel"}
    ]


def test_download_history_filters_and_traffic_total(tmp_path: Path):
    database = Database(tmp_path / "app.db")
    database.initialize()
    chat = database.upsert_chat("@history", "History")
    completed_id = database.create_download(
        chat["id"], "History", 1, "/downloads/one.mp4", "video", 100
    )
    failed_id = database.create_download(
        chat["id"], "History", 2, "/downloads/two.mp4", "video", 200
    )
    skipped_id = database.create_download(
        chat["id"], "History", 3, "/downloads/three.mp4", "video", 300
    )
    database.update_download(completed_id, status="completed", downloaded_size=100)
    database.update_download(failed_id, status="failed", downloaded_size=50)
    database.update_download(skipped_id, status="skipped", downloaded_size=300)

    success = database.recent_downloads(
        terminal_only=True,
        statuses=("completed", "skipped"),
    )
    failed = database.recent_downloads(
        terminal_only=True,
        statuses=("failed",),
    )

    assert {item["id"] for item in success} == {completed_id, skipped_id}
    assert [item["id"] for item in failed] == [failed_id]
    assert database.count_downloads(
        terminal_only=True,
        statuses=("failed",),
    ) == 1
    assert database.download_traffic_bytes() == 450


def test_database_backup_preserves_settings_and_channels(tmp_path: Path):
    database = Database(tmp_path / "app.db")
    database.initialize()
    database.set_setting("admin_password_hash", "hash")
    database.upsert_chat("@persisted", "Persisted Channel")
    backup_path = tmp_path / "app.db.previous"

    database.backup_to(backup_path)

    backup = Database(backup_path)
    assert backup.get_setting("admin_password_hash") == "hash"
    assert [chat["chat_ref"] for chat in backup.list_chats()] == ["@persisted"]
