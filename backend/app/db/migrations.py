from __future__ import annotations

import logging
import shutil
import sqlite3
from typing import Callable

from .utils import utc_now

logger = logging.getLogger("canvas_helper.db")


class MigrationsMixin:
    """Schema version management and forward migrations for ``Database``."""

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
        if 0 < current_version < self.CURRENT_SCHEMA_VERSION:
            self._backup_before_migration(current_version)

        for target_version, migration in self._migrations():
            if current_version < target_version:
                migration(conn)
                conn.execute(f"PRAGMA user_version = {target_version}")
                current_version = target_version
        if current_version >= 3 and self._create_search_index_schema(conn):
            self._rebuild_search_index(conn)

    def _backup_before_migration(self, from_version: int) -> None:
        """Copy the existing populated DB file before upgrading its schema (4.9)."""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return
        backup_dir = self.path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = utc_now().replace(":", "").replace("-", "").replace(".", "")
        backup_path = backup_dir / f"{self.path.stem}.v{from_version}.{stamp}.bak"
        try:
            shutil.copy2(self.path, backup_path)
            logger.info("Backed up database to %s before migrating from v%s", backup_path, from_version)
        except OSError as exc:
            logger.warning("Could not back up database before migration: %s", exc)

    def _schema_version(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    def _migrations(self) -> tuple[tuple[int, Callable[[sqlite3.Connection], None]], ...]:
        return (
            (1, self._create_initial_schema),
            (2, self._create_performance_indexes),
            (3, self._create_search_index),
            (4, self._rebuild_search_index),
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

            CREATE TABLE IF NOT EXISTS analyses (
                course_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                content_json TEXT NOT NULL,
                model TEXT,
                generated_at TEXT NOT NULL,
                PRIMARY KEY(course_id, kind),
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
