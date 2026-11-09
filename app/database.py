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


_UNSET = object()
DIALOG_CACHE_KEY = "telegram_dialog_cache"


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
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def backup_to(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as source, sqlite3.connect(destination) as target:
            source.backup(target)

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
            download_filter TEXT NOT NULL DEFAULT '',
            visible INTEGER NOT NULL DEFAULT 1,
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
            attempt_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            error TEXT,
            error_code TEXT,
            next_retry_at TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE,
            UNIQUE(chat_id, message_id)
        );

        CREATE TABLE IF NOT EXISTS forward_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_chat TEXT NOT NULL,
            source_title TEXT NOT NULL DEFAULT '',
            target_chat TEXT NOT NULL,
            target_title TEXT NOT NULL DEFAULT '',
            filter_expression TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            last_message_id INTEGER NOT NULL DEFAULT 0,
            forwarded_count INTEGER NOT NULL DEFAULT 0,
            last_checked_at TEXT,
            last_forwarded_at TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_downloads_started_at
            ON downloads(started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_downloads_status_started_at
            ON downloads(status, started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_forward_rules_enabled
            ON forward_rules(enabled);
        """
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(schema)
            chat_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(chats)")
            }
            if "download_filter" not in chat_columns:
                connection.execute(
                    "ALTER TABLE chats ADD COLUMN download_filter TEXT NOT NULL DEFAULT ''"
                )
            if "visible" not in chat_columns:
                connection.execute(
                    "ALTER TABLE chats ADD COLUMN visible INTEGER NOT NULL DEFAULT 1"
                )
            download_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(downloads)")
            }
            if "attempt_count" not in download_columns:
                connection.execute(
                    "ALTER TABLE downloads ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0"
                )
            if "error_code" not in download_columns:
                connection.execute("ALTER TABLE downloads ADD COLUMN error_code TEXT")
            if "next_retry_at" not in download_columns:
                connection.execute("ALTER TABLE downloads ADD COLUMN next_retry_at TEXT")

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

    def get_dialog_cache(self) -> dict[str, Any]:
        cached = self.get_setting(DIALOG_CACHE_KEY, {})
        if not isinstance(cached, dict):
            return {"dialogs": [], "cached_at": 0}
        dialogs = cached.get("dialogs", [])
        if not isinstance(dialogs, list):
            dialogs = []
        cleaned = [
            dialog
            for dialog in dialogs
            if isinstance(dialog, dict) and dialog.get("id") and dialog.get("title")
        ]
        try:
            cached_at = int(cached.get("cached_at", 0) or 0)
        except (TypeError, ValueError):
            cached_at = 0
        return {"dialogs": cleaned, "cached_at": cached_at}

    def save_dialog_cache(self, dialogs: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {
            "dialogs": dialogs,
            "cached_at": int(datetime.now(UTC).timestamp() * 1000),
        }
        self.set_setting(DIALOG_CACHE_KEY, payload)
        return payload

    def list_chats(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM chats WHERE visible = 1 ORDER BY created_at ASC, id ASC"
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
        download_filter: str = "",
    ) -> dict[str, Any]:
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO chats(
                    chat_ref, title, date_from, date_to, download_filter,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_ref) DO UPDATE SET
                    title = CASE WHEN excluded.title <> '' THEN excluded.title ELSE chats.title END,
                    visible = 1,
                    date_from = excluded.date_from,
                    date_to = excluded.date_to,
                    download_filter = CASE
                        WHEN excluded.download_filter <> '' THEN excluded.download_filter
                        ELSE chats.download_filter
                    END,
                    updated_at = excluded.updated_at
                """,
                (chat_ref, title, date_from, date_to, download_filter, now, now),
            )
            row = connection.execute(
                "SELECT * FROM chats WHERE chat_ref = ?", (chat_ref,)
            ).fetchone()
        return dict(row)

    def ensure_download_chat(self, chat_ref: str, title: str = "") -> dict[str, Any]:
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO chats(
                    chat_ref, title, enabled, visible, created_at, updated_at
                )
                VALUES (?, ?, 0, 0, ?, ?)
                ON CONFLICT(chat_ref) DO UPDATE SET
                    title = CASE WHEN excluded.title <> '' THEN excluded.title ELSE chats.title END,
                    updated_at = excluded.updated_at
                """,
                (chat_ref, title, now, now),
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
        date_from: Any = _UNSET,
        date_to: Any = _UNSET,
        download_filter: Any = _UNSET,
    ) -> None:
        fields: list[str] = []
        values: list[Any] = []
        if title is not None:
            fields.append("title = ?")
            values.append(title)
        if enabled is not None:
            fields.append("enabled = ?")
            values.append(1 if enabled else 0)
        if date_from is not _UNSET:
            fields.append("date_from = ?")
            values.append(date_from or None)
        if date_to is not _UNSET:
            fields.append("date_to = ?")
            values.append(date_to or None)
        if download_filter is not _UNSET:
            fields.append("download_filter = ?")
            values.append(download_filter or "")
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

    def list_forward_rules(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE enabled = 1" if enabled_only else ""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM forward_rules {where} ORDER BY created_at ASC, id ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_forward_rule(self, rule_id: int) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM forward_rules WHERE id = ?", (rule_id,)
            ).fetchone()
        return dict(row) if row else None

    def create_forward_rule(
        self,
        *,
        source_chat: str,
        source_title: str,
        target_chat: str,
        target_title: str,
        filter_expression: str,
        enabled: bool,
        last_message_id: int,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO forward_rules(
                    source_chat, source_title, target_chat, target_title,
                    filter_expression, enabled, last_message_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_chat,
                    source_title,
                    target_chat,
                    target_title,
                    filter_expression,
                    int(enabled),
                    last_message_id,
                    now,
                    now,
                ),
            )
            rule_id = int(cursor.lastrowid)
        rule = self.get_forward_rule(rule_id)
        if rule is None:
            raise RuntimeError("Unable to create forward rule")
        return rule

    def update_forward_rule(self, rule_id: int, **updates: Any) -> dict[str, Any] | None:
        allowed = {
            "source_chat",
            "source_title",
            "target_chat",
            "target_title",
            "filter_expression",
            "enabled",
            "last_message_id",
        }
        fields: list[str] = []
        values: list[Any] = []
        for key, value in updates.items():
            if key not in allowed or value is None:
                continue
            fields.append(f"{key} = ?")
            values.append(int(value) if key in {"enabled", "last_message_id"} else value)
        if not fields:
            return self.get_forward_rule(rule_id)
        fields.append("updated_at = ?")
        values.extend((utc_now(), rule_id))
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE forward_rules SET {', '.join(fields)} WHERE id = ?", values
            )
        return self.get_forward_rule(rule_id)

    def delete_forward_rule(self, rule_id: int) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM forward_rules WHERE id = ?", (rule_id,)
            )
        return cursor.rowcount > 0

    def record_forward_result(
        self,
        rule_id: int,
        *,
        last_message_id: int,
        forwarded_count: int = 0,
        error: str | None = None,
    ) -> None:
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE forward_rules
                SET last_message_id = MAX(last_message_id, ?),
                    forwarded_count = forwarded_count + ?,
                    last_checked_at = ?,
                    last_forwarded_at = CASE WHEN ? > 0 THEN ? ELSE last_forwarded_at END,
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    last_message_id,
                    max(0, forwarded_count),
                    now,
                    forwarded_count,
                    now,
                    error,
                    now,
                    rule_id,
                ),
            )

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
            connection.execute(
                """
                INSERT INTO downloads(
                    chat_id, chat_title, message_id, file_name, media_type,
                    total_size, downloaded_size, attempt_count, status, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, 'downloading', ?)
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                    chat_title = excluded.chat_title,
                    file_name = excluded.file_name,
                    media_type = excluded.media_type,
                    total_size = excluded.total_size,
                    status = 'downloading',
                    error = NULL,
                    error_code = NULL,
                    next_retry_at = NULL,
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
            row = connection.execute(
                "SELECT id FROM downloads WHERE chat_id = ? AND message_id = ?",
                (chat_id, message_id),
            ).fetchone()
            if row is not None:
                return int(row["id"])
        raise RuntimeError("Unable to create download record")

    def update_download(
        self,
        download_id: int,
        *,
        status: str | None = None,
        downloaded_size: int | None = None,
        file_name: str | None = None,
        error: str | None | object = _UNSET,
        error_code: str | None | object = _UNSET,
        attempt_count: int | None = None,
        next_retry_at: str | None | object = _UNSET,
    ) -> None:
        fields: list[str] = []
        values: list[Any] = []
        if status is not None:
            fields.append("status = ?")
            values.append(status)
            if status in {"completed", "skipped", "failed"}:
                fields.append("finished_at = ?")
                values.append(utc_now())
            else:
                fields.append("finished_at = NULL")
        if downloaded_size is not None:
            fields.append("downloaded_size = ?")
            values.append(downloaded_size)
        if file_name is not None:
            fields.append("file_name = ?")
            values.append(file_name)
        if attempt_count is not None:
            fields.append("attempt_count = ?")
            values.append(max(0, int(attempt_count)))
        if error is not _UNSET:
            fields.append("error = ?")
            values.append(error)
        if error_code is not _UNSET:
            fields.append("error_code = ?")
            values.append(error_code)
        if next_retry_at is not _UNSET:
            fields.append("next_retry_at = ?")
            values.append(next_retry_at)
        if not fields:
            return
        values.append(download_id)
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE downloads SET {', '.join(fields)} WHERE id = ?", values
            )

    def get_download(self, download_id: int) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM downloads WHERE id = ?", (download_id,)
            ).fetchone()
        return dict(row) if row else None

    def resumable_downloads(self, chat_id: int) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM downloads
                WHERE chat_id = ? AND status IN ('downloading', 'retry_wait')
                ORDER BY message_id ASC
                """,
                (chat_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def download_counts(self) -> dict[str, int]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM downloads GROUP BY status"
            ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def download_traffic_bytes(self) -> int:
        """Return the total bytes recorded across active and historical downloads."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(downloaded_size), 0) AS total FROM downloads"
            ).fetchone()
        return int(row["total"] or 0)

    def requeue_failed_downloads(self, limit: int = 100) -> int:
        """Requeue a bounded number of terminal failures for a manual retry."""
        limit = max(1, min(int(limit), 500))
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE downloads
                SET status = 'retry_wait',
                    error = NULL,
                    error_code = NULL,
                    next_retry_at = NULL,
                    attempt_count = 0,
                    finished_at = NULL
                WHERE id IN (
                    SELECT id FROM downloads
                    WHERE status = 'failed'
                    ORDER BY finished_at DESC, id DESC
                    LIMIT ?
                )
                """,
                (limit,),
            )
            return max(0, cursor.rowcount)

    def recent_downloads(
        self,
        limit: int = 100,
        offset: int = 0,
        *,
        terminal_only: bool = False,
        statuses: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if terminal_only:
            clauses.append("status NOT IN ('downloading', 'retry_wait')")
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            values.extend(statuses)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.extend((limit, max(0, offset)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM downloads {where} ORDER BY started_at DESC LIMIT ? OFFSET ?",
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def count_downloads(
        self,
        *,
        terminal_only: bool = False,
        statuses: tuple[str, ...] | None = None,
    ) -> int:
        clauses: list[str] = []
        values: list[Any] = []
        if terminal_only:
            clauses.append("status NOT IN ('downloading', 'retry_wait')")
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            values.extend(statuses)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS count FROM downloads{where}", values
            ).fetchone()
        return int(row["count"])
