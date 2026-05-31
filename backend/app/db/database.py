from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .migrations import MigrationsMixin
from .search import SearchMixin
from .utils import row_to_dict, rows_to_dicts, utc_now


class Database(MigrationsMixin, SearchMixin):
    CURRENT_SCHEMA_VERSION = 4

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def _conn(self, conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
        """Reuse a caller-supplied connection, or open a self-managed one (4.3).

        When ``conn`` is provided the caller owns its lifecycle (commit/close);
        batch operations pass one connection to avoid reopening it per row.
        """
        if conn is not None:
            yield conn
        else:
            with self.connect() as owned:
                yield owned

    # Settings -----------------------------------------------------------------

    def get_setting(self, key: str, default: str | None = None, *, conn: sqlite3.Connection | None = None) -> str | None:
        with self._conn(conn) as c:
            row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def put_settings(self, values: dict[str, str], *, conn: sqlite3.Connection | None = None) -> None:
        now = utc_now()
        with self._conn(conn) as c:
            for key, value in values.items():
                c.execute(
                    """
                    INSERT INTO settings(key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value,
                        updated_at=excluded.updated_at
                    """,
                    (key, value, now),
                )

    # Sync runs ----------------------------------------------------------------

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

    # Analyses -----------------------------------------------------------------

    def get_analysis(self, course_id: int, kind: str = "course_overview") -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT content_json FROM analyses WHERE course_id = ? AND kind = ?",
                (course_id, kind),
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["content_json"])
        except (json.JSONDecodeError, TypeError):
            return None

    # Event log ----------------------------------------------------------------

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
        conn: sqlite3.Connection | None = None,
    ) -> None:
        with self._conn(conn) as c:
            cursor = c.execute(
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
            # Trim periodically: a full anti-join on every insert is wasteful.
            if cursor.lastrowid and cursor.lastrowid % 200 == 0:
                c.execute(
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
