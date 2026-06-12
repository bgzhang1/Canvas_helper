from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    CURRENT_SCHEMA_VERSION = 3

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            self._migrate(conn)
            self._ensure_defaults(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        current_version = self._schema_version(conn)
        if current_version > self.CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {current_version} is newer than "
                f"this app supports ({self.CURRENT_SCHEMA_VERSION})."
            )

        for target_version, migration in self._migrations():
            if current_version < target_version:
                migration(conn)
                conn.execute(f"PRAGMA user_version = {target_version}")
                current_version = target_version

    def _schema_version(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    def _migrations(self) -> tuple[tuple[int, Callable[[sqlite3.Connection], None]], ...]:
        return (
            (1, self._create_initial_schema),
            (2, self._create_performance_indexes),
            (3, self._mark_legacy_schema_current),
        )

    def _create_initial_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS courses (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                course_code TEXT,
                workflow_state TEXT,
                term_name TEXT,
                raw_json TEXT NOT NULL,
                synced_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY,
                course_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                message TEXT,
                posted_at TEXT,
                author_name TEXT,
                raw_json TEXT NOT NULL,
                synced_at TEXT NOT NULL,
                FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS assignments (
                id INTEGER PRIMARY KEY,
                course_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                due_at TEXT,
                unlock_at TEXT,
                lock_at TEXT,
                workflow_state TEXT,
                points_possible REAL,
                raw_json TEXT NOT NULL,
                synced_at TEXT NOT NULL,
                FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS calendar_events (
                id INTEGER PRIMARY KEY,
                course_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                start_at TEXT,
                end_at TEXT,
                event_type TEXT,
                raw_json TEXT NOT NULL,
                synced_at TEXT NOT NULL,
                FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS pages (
                course_id INTEGER NOT NULL,
                page_url TEXT NOT NULL,
                page_id INTEGER,
                title TEXT NOT NULL,
                body TEXT,
                updated_at TEXT,
                published INTEGER,
                raw_json TEXT NOT NULL,
                synced_at TEXT NOT NULL,
                PRIMARY KEY(course_id, page_url),
                FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS course_people (
                course_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                sortable_name TEXT,
                email TEXT,
                role TEXT,
                last_activity_at TEXT,
                raw_json TEXT NOT NULL,
                synced_at TEXT NOT NULL,
                PRIMARY KEY(course_id, user_id),
                FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY,
                course_id INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                filename TEXT NOT NULL,
                content_type TEXT,
                size INTEGER,
                updated_at TEXT,
                canvas_url TEXT,
                local_path TEXT,
                sha256 TEXT,
                backup_status TEXT NOT NULL DEFAULT 'pending',
                backup_error TEXT,
                downloaded_at TEXT,
                downloaded_canvas_updated_at TEXT,
                extraction_status TEXT NOT NULL DEFAULT 'pending',
                extraction_error TEXT,
                extracted_text_path TEXT,
                outline_json TEXT,
                extracted_at TEXT,
                raw_json TEXT NOT NULL,
                synced_at TEXT NOT NULL,
                FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                message TEXT,
                counts_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS event_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                category TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT NOT NULL,
                course_id INTEGER,
                course_name TEXT,
                item_id TEXT,
                item_name TEXT,
                message TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_event_logs_created_at
            ON event_logs(created_at DESC);
            """
        )
        self._create_performance_indexes(conn)

    def _create_performance_indexes(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_courses_term_name_name
            ON courses(term_name DESC, name);

            CREATE INDEX IF NOT EXISTS idx_announcements_course_posted
            ON announcements(course_id, posted_at DESC);

            CREATE INDEX IF NOT EXISTS idx_assignments_course_dates
            ON assignments(course_id, due_at, unlock_at, lock_at, name);

            CREATE INDEX IF NOT EXISTS idx_calendar_events_course_dates
            ON calendar_events(course_id, start_at, end_at);

            CREATE INDEX IF NOT EXISTS idx_pages_course_updated
            ON pages(course_id, updated_at DESC, title);

            CREATE INDEX IF NOT EXISTS idx_course_people_course_role_name
            ON course_people(course_id, role, sortable_name, name);

            CREATE INDEX IF NOT EXISTS idx_files_course_updated
            ON files(course_id, updated_at DESC, display_name);

            CREATE INDEX IF NOT EXISTS idx_files_course_backup_status
            ON files(course_id, backup_status);
            """
        )

    def _mark_legacy_schema_current(self, conn: sqlite3.Connection) -> None:
        return None

    def _ensure_defaults(self, conn: sqlite3.Connection) -> None:
        self.set_default(conn, "sync.enabled", "false")
        self.set_default(conn, "sync.interval_minutes", "60")

    def set_default(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO settings(key, value, updated_at)
            VALUES (?, ?, ?)
            """,
            (key, value, utc_now()),
        )

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def put_settings(self, values: dict[str, str]) -> None:
        now = utc_now()
        with self.connect() as conn:
            for key, value in values.items():
                conn.execute(
                    """
                    INSERT INTO settings(key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value,
                        updated_at=excluded.updated_at
                    """,
                    (key, value, now),
                )

    def start_sync_run(self) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO sync_runs(started_at, status, counts_json)
                VALUES (?, 'running', '{}')
                """,
                (utc_now(),),
            )
            return int(cursor.lastrowid)

    def finish_sync_run(
        self,
        run_id: int,
        status: str,
        message: str | None = None,
        counts: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE sync_runs
                SET finished_at = ?, status = ?, message = ?, counts_json = ?
                WHERE id = ?
                """,
                (utc_now(), status, message, json.dumps(counts or {}), run_id),
            )

    def update_sync_run_counts(
        self,
        run_id: int,
        counts: dict[str, Any],
        message: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE sync_runs
                SET counts_json = ?, message = COALESCE(?, message)
                WHERE id = ? AND status = 'running'
                """,
                (json.dumps(counts, ensure_ascii=False), message, run_id),
            )

    def latest_sync_run(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return row_to_dict(row) if row else None

    def mark_stale_sync_runs_interrupted(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE sync_runs
                SET finished_at = ?, status = 'interrupted', message = 'Server restarted before this sync finished.'
                WHERE status = 'running' AND finished_at IS NULL
                """,
                (utc_now(),),
            )

    def mark_stale_downloads_interrupted(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE files
                SET backup_status = 'fail_download',
                    backup_error = 'Server restarted while this file was downloading.'
                WHERE backup_status = 'downloading'
                """
            )

    def add_event(
        self,
        *,
        category: str,
        action: str,
        status: str,
        title: str,
        course_id: int | None = None,
        course_name: str | None = None,
        item_id: str | int | None = None,
        item_name: str | None = None,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO event_logs(
                    created_at, category, action, status, title,
                    course_id, course_name, item_id, item_name, message, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_now(),
                    category,
                    action,
                    status,
                    title,
                    course_id,
                    course_name,
                    str(item_id) if item_id is not None else None,
                    item_name,
                    message,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            conn.execute(
                """
                DELETE FROM event_logs
                WHERE id NOT IN (
                    SELECT id FROM event_logs ORDER BY id DESC LIMIT 2000
                )
                """
            )

    def list_events(self, limit: int = 200, *, category: str | None = None) -> list[dict[str, Any]]:
        capped_limit = max(1, min(limit, 500))
        with self.connect() as conn:
            if category:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM event_logs
                    WHERE category = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (category, capped_limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM event_logs
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (capped_limit,),
                ).fetchall()
        items = rows_to_dicts(rows)
        for item in items:
            try:
                item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            except json.JSONDecodeError:
                item["metadata"] = {}
        return items


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(row) for row in rows if row is not None]
