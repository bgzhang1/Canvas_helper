from __future__ import annotations

import sqlite3

import pytest

from backend.app.db import Database, utc_now


def schema_version(path) -> int:
    with sqlite3.connect(path) as conn:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])


def table_names(path) -> set[str]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
    return {row[0] for row in rows}


def index_names(path) -> set[str]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'index' AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
    return {row[0] for row in rows}


def test_init_creates_schema_and_sets_user_version(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    db = Database(db_path)

    db.init()

    assert schema_version(db_path) == Database.CURRENT_SCHEMA_VERSION
    assert {
        "settings",
        "courses",
        "announcements",
        "assignments",
        "calendar_events",
        "pages",
        "course_people",
        "files",
        "sync_runs",
        "event_logs",
    }.issubset(table_names(db_path))
    assert {
        "idx_courses_term_name_name",
        "idx_announcements_course_posted",
        "idx_assignments_course_dates",
        "idx_calendar_events_course_dates",
        "idx_pages_course_updated",
        "idx_course_people_course_role_name",
        "idx_files_course_updated",
        "idx_files_course_backup_status",
    }.issubset(index_names(db_path))
    assert db.get_setting("sync.enabled") == "false"
    assert db.get_setting("sync.interval_minutes") == "60"


def test_init_is_idempotent_and_preserves_existing_data(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    db = Database(db_path)
    db.init()
    db.put_settings({"sync.enabled": "true"})

    db.init()

    assert schema_version(db_path) == Database.CURRENT_SCHEMA_VERSION
    assert db.get_setting("sync.enabled") == "true"


def test_init_marks_existing_version_zero_schema_as_current(tmp_path) -> None:
    db_path = tmp_path / "legacy.db"
    legacy_db = Database(db_path)
    now = utc_now()
    with legacy_db.connect() as conn:
        legacy_db._create_initial_schema(conn)
        conn.execute(
            """
            INSERT INTO settings(key, value, updated_at)
            VALUES ('sync.enabled', 'true', ?)
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO courses(id, name, raw_json, synced_at)
            VALUES (1, 'Legacy Course', '{}', ?)
            """,
            (now,),
        )
        conn.execute("PRAGMA user_version = 0")

    db = Database(db_path)
    db.init()

    assert schema_version(db_path) == Database.CURRENT_SCHEMA_VERSION
    assert db.get_setting("sync.enabled") == "true"
    with db.connect() as conn:
        row = conn.execute("SELECT name FROM courses WHERE id = 1").fetchone()
    assert row["name"] == "Legacy Course"


def test_init_rejects_newer_schema_version(tmp_path) -> None:
    db_path = tmp_path / "future.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"PRAGMA user_version = {Database.CURRENT_SCHEMA_VERSION + 1}")

    db = Database(db_path)

    with pytest.raises(RuntimeError, match="newer than this app supports"):
        db.init()
