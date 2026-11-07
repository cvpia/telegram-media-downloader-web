"""Small SQLite persistence layer for settings, chats and download history."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    """Thread-safe SQLite wrapper.

    The workload is intentionally small, so synchronous SQLite calls are kept
    behind a lock instead of introducing an ORM and another async layer.
    """

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        schema = """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chats (
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

        CREATE TABLE IF NOT EXISTS downloads (
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
            FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE,
            UNIQUE(chat_id, message_id)
        );

        CREATE INDEX IF NOT EXISTS idx_downloads_started_at
            ON downloads(started_at DESC);
        """
        with self._lock, self._connect() as connection:
            connection.executescript(schema)

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return default
        return json.loads(row["value"])

    def set_setting(self, key: str, value: Any) -> None:
        encoded = json.dumps(value, ensure_ascii=False)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO app_settings(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, encoded, utc_now()),
            )

    def set_settings(self, values: dict[str, Any]) -> None:
        now = utc_now()
        rows = [(key, json.dumps(value, ensure_ascii=False), now) for key, value in values.items()]
        with self._lock, self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO app_settings(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                rows,
            )

    def list_chats(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM chats ORDER BY created_at ASC, id ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_chat(self, chat_id: int) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM chats WHERE id = ?", (chat_id,)
            ).fetchone()
        return dict(row) if row else None

    def upsert_chat(
        self,
        chat_ref: str,
        title: str = "",
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO chats(chat_ref, title, date_from, date_to, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_ref) DO UPDATE SET
                    title = CASE WHEN excluded.title <> '' THEN excluded.title ELSE chats.title END,
                    date_from = excluded.date_from,
                    date_to = excluded.date_to,
                    updated_at = excluded.updated_at
                """,
                (chat_ref, title, date_from, date_to, now, now),
            )
            row = connection.execute(
                "SELECT * FROM chats WHERE chat_ref = ?", (chat_ref,)
            ).fetchone()
        return dict(row)

    def update_chat_metadata(
        self,
        chat_id: int,
        *,
        title: str | None = None,
        enabled: bool | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> None:
        fields: list[str] = []
        values: list[Any] = []
        if title is not None:
            fields.append("title = ?")
            values.append(title)
        if enabled is not None:
            fields.append("enabled = ?")
            values.append(1 if enabled else 0)
        if date_from is not None:
            fields.append("date_from = ?")
            values.append(date_from or None)
        if date_to is not None:
            fields.append("date_to = ?")
            values.append(date_to or None)
        if not fields:
            return
        fields.append("updated_at = ?")
        values.append(utc_now())
        values.append(chat_id)
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE chats SET {', '.join(fields)} WHERE id = ?", values
            )

    def set_last_message(self, chat_id: int, message_id: int) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE chats
                SET last_message_id = MAX(last_message_id, ?), updated_at = ?
                WHERE id = ?
                """,
                (message_id, utc_now(), chat_id),
            )

    def delete_chat(self, chat_id: int) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        return cursor.rowcount > 0

    def create_download(
        self,
        chat_id: int,
        chat_title: str,
        message_id: int,
        file_name: str,
        media_type: str,
        total_size: int,
    ) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO downloads(
                    chat_id, chat_title, message_id, file_name, media_type,
                    total_size, downloaded_size, status, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, 'downloading', ?)
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                    chat_title = excluded.chat_title,
                    file_name = excluded.file_name,
                    media_type = excluded.media_type,
                    total_size = excluded.total_size,
                    downloaded_size = 0,
                    status = 'downloading',
                    error = NULL,
                    started_at = excluded.started_at,
                    finished_at = NULL
                """,
                (
                    chat_id,
                    chat_title,
                    message_id,
                    file_name,
                    media_type,
                    total_size,
                    utc_now(),
                ),
            )
            if cursor.lastrowid:
                row = connection.execute(
                    "SELECT id FROM downloads WHERE chat_id = ? AND message_id = ?",
                    (chat_id, message_id),
                ).fetchone()
                return int(row["id"])
        raise RuntimeError("Unable to create download record")

    def update_download(
        self,
        download_id: int,
        *,
        status: str | None = None,
        downloaded_size: int | None = None,
        file_name: str | None = None,
        error: str | None = None,
    ) -> None:
        fields: list[str] = []
        values: list[Any] = []
        if status is not None:
            fields.append("status = ?")
            values.append(status)
            if status in {"completed", "skipped", "failed"}:
                fields.append("finished_at = ?")
                values.append(utc_now())
        if downloaded_size is not None:
            fields.append("downloaded_size = ?")
            values.append(downloaded_size)
        if file_name is not None:
            fields.append("file_name = ?")
            values.append(file_name)
        if error is not None:
            fields.append("error = ?")
            values.append(error)
        if not fields:
            return
        values.append(download_id)
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE downloads SET {', '.join(fields)} WHERE id = ?", values
            )

    def recent_downloads(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM downloads ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]