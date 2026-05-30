from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

from ..db import rows_to_dicts
from ..runtime import state
from .files import list_course_files_from_db

router = APIRouter()


@router.get("/api/courses")
async def courses() -> list[dict[str, Any]]:
    def query() -> list[dict[str, Any]]:
        # "Ongoing schedule" = dated timeline points (assignments / calendar / announcements)
        # that are not historical, mirroring the timeline view's >7-day-past cutoff.
        threshold = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with state().db.connect() as conn:
            rows = conn.execute(
                """
                SELECT c.*,
                       (SELECT COUNT(*) FROM announcements a WHERE a.course_id = c.id) AS announcement_count,
                       (SELECT COUNT(*) FROM assignments s WHERE s.course_id = c.id) AS assignment_count,
                       (SELECT COUNT(*) FROM files f WHERE f.course_id = c.id) AS file_count,
                       (SELECT COUNT(*) FROM files f WHERE f.course_id = c.id AND f.backup_status = 'downloaded') AS downloaded_count,
                       (
                         (SELECT COUNT(*) FROM assignments s
                          WHERE s.course_id = c.id AND s.due_at IS NOT NULL AND s.due_at >= ?)
                         + (SELECT COUNT(*) FROM calendar_events e
                          WHERE e.course_id = c.id AND COALESCE(e.start_at, e.end_at) IS NOT NULL AND COALESCE(e.start_at, e.end_at) >= ?)
                         + (SELECT COUNT(*) FROM announcements a
                          WHERE a.course_id = c.id AND a.posted_at IS NOT NULL AND a.posted_at >= ?)
                       ) AS upcoming_count
                FROM courses c
                """,
                (threshold, threshold, threshold),
            ).fetchall()
        items = rows_to_dicts(rows)
        for item in items:
            term = _parse_raw_json(item.pop("raw_json", None)).get("term") or {}
            item["term_id"] = term.get("id")
            item["term_start_at"] = term.get("start_at")
            item["term_end_at"] = term.get("end_at")
        # Sort by term start date (newest first), then course name; courses with
        # no term sort last. term_name is NOT chronological -- "Semester B 2024/25"
        # would sort above "Semester A 2025/26" -- so the frontend groups and
        # orders semesters on these dates instead.
        items.sort(key=lambda c: (c.get("name") or "").lower())
        items.sort(key=lambda c: c.get("term_start_at") or "", reverse=True)
        return items

    return await run_in_threadpool(query)


@router.get("/api/courses/{course_id}/announcements")
async def announcements(course_id: int) -> list[dict[str, Any]]:
    def query() -> list[dict[str, Any]]:
        with state().db.connect() as conn:
            return list_announcements_from_db(conn, course_id)

    return await run_in_threadpool(query)


@router.get("/api/courses/{course_id}/assignments")
async def assignments(course_id: int) -> list[dict[str, Any]]:
    def query() -> list[dict[str, Any]]:
        with state().db.connect() as conn:
            return list_assignments_from_db(conn, course_id)

    return await run_in_threadpool(query)


@router.get("/api/courses/{course_id}/people")
async def people(course_id: int) -> list[dict[str, Any]]:
    def query() -> list[dict[str, Any]]:
        with state().db.connect() as conn:
            return list_people_from_db(conn, course_id)

    return await run_in_threadpool(query)


@router.get("/api/courses/{course_id}/home")
async def course_home(course_id: int) -> dict[str, Any] | None:
    def query() -> dict[str, Any] | None:
        with state().db.connect() as conn:
            return select_course_home_from_db(conn, course_id)

    return await run_in_threadpool(query)


@router.get("/api/courses/{course_id}/timeline")
async def timeline(course_id: int) -> dict[str, Any]:
    def query() -> dict[str, Any]:
        with state().db.connect() as conn:
            return build_timeline_from_db(conn, course_id)

    return await run_in_threadpool(query)


@router.get("/api/courses/{course_id}/detail")
async def course_detail(course_id: int) -> dict[str, Any]:
    def query() -> dict[str, Any]:
        with state().db.connect() as conn:
            return {
                "announcements": list_announcements_from_db(conn, course_id),
                "assignments": list_assignments_from_db(conn, course_id),
                "files": list_course_files_from_db(conn, course_id),
                "people": list_people_from_db(conn, course_id),
                "timeline": build_timeline_from_db(conn, course_id),
                "home": select_course_home_from_db(conn, course_id),
            }

    return await run_in_threadpool(query)


def list_announcements_from_db(conn: sqlite3.Connection, course_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, title, message, posted_at, author_name, raw_json
        FROM announcements
        WHERE course_id = ?
        ORDER BY posted_at DESC
        """,
        (course_id,),
    ).fetchall()
    items = rows_to_dicts(rows)
    for item in items:
        raw = _parse_raw_json(item.pop("raw_json", None))
        item["html_url"] = raw.get("html_url") or raw.get("url")
    return items


def list_assignments_from_db(conn: sqlite3.Connection, course_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, name, due_at, unlock_at, lock_at, workflow_state, points_possible, raw_json
        FROM assignments
        WHERE course_id = ?
        ORDER BY COALESCE(due_at, unlock_at, lock_at), name
        """,
        (course_id,),
    ).fetchall()
    items = rows_to_dicts(rows)
    for item in items:
        raw = _parse_raw_json(item.pop("raw_json", None))
        submission = raw.get("submission") if isinstance(raw.get("submission"), dict) else {}
        item["description"] = raw.get("description")
        item["html_url"] = raw.get("html_url")
        item["submission_types"] = _string_list(raw.get("submission_types"))
        item["allowed_extensions"] = _string_list(raw.get("allowed_extensions"))
        item["assignment_group_name"] = raw.get("assignment_group_name")
        item["created_at"] = raw.get("created_at")
        item["updated_at"] = raw.get("updated_at")
        item["score"] = submission.get("score")
        item["grade"] = submission.get("grade")
        item["submitted_at"] = submission.get("submitted_at")
        item["submission_workflow_state"] = submission.get("workflow_state")
    return items


def list_people_from_db(conn: sqlite3.Connection, course_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT user_id AS id, name, sortable_name, email, role, last_activity_at
        FROM course_people
        WHERE course_id = ?
        ORDER BY
          CASE
            WHEN COALESCE(role, '') LIKE '%Teacher%' OR COALESCE(role, '') LIKE '%Instructor%' THEN 0
            WHEN COALESCE(role, '') LIKE '%Ta%' THEN 1
            ELSE 2
          END,
          COALESCE(sortable_name, name)
        """,
        (course_id,),
    ).fetchall()
    return rows_to_dicts(rows)


def select_course_home_from_db(conn: sqlite3.Connection, course_id: int) -> dict[str, Any] | None:
    rows = rows_to_dicts(
        conn.execute(
            """
            SELECT page_url, page_id, title, body, updated_at, published, raw_json
            FROM pages
            WHERE course_id = ?
            ORDER BY updated_at DESC, title
            """,
            (course_id,),
        ).fetchall()
    )
    if not rows:
        return None

    def score(page: dict[str, Any]) -> int:
        raw = _parse_raw_json(page.get("raw_json"))
        title = (page.get("title") or "").lower()
        page_url = (page.get("page_url") or "").lower()
        if raw.get("front_page") is True:
            return 0
        if page_url in {"front-page", "home", "index"}:
            return 1
        if "home" in title or "front page" in title:
            return 2
        return 3

    selected = sorted(rows, key=score)[0]
    selected.pop("raw_json", None)
    return selected


def build_timeline_from_db(conn: sqlite3.Connection, course_id: int) -> dict[str, Any]:
    analysis = get_analysis_from_db(conn, course_id)
    assignments = rows_to_dicts(
        conn.execute(
            """
            SELECT id AS item_id, name AS title, due_at AS date, 'assignment' AS source
            FROM assignments
            WHERE course_id = ? AND due_at IS NOT NULL
            """,
            (course_id,),
        ).fetchall()
    )
    announcements = rows_to_dicts(
        conn.execute(
            """
            SELECT id AS item_id, title, posted_at AS date, 'announcement' AS source
            FROM announcements
            WHERE course_id = ? AND posted_at IS NOT NULL
            """,
            (course_id,),
        ).fetchall()
    )
    calendar = rows_to_dicts(
        conn.execute(
            """
            SELECT id AS item_id, title, COALESCE(start_at, end_at) AS date, 'calendar' AS source
            FROM calendar_events
            WHERE course_id = ? AND COALESCE(start_at, end_at) IS NOT NULL
            """,
            (course_id,),
        ).fetchall()
    )
    announcement_count = conn.execute(
        "SELECT COUNT(*) AS count FROM announcements WHERE course_id = ?",
        (course_id,),
    ).fetchone()["count"]
    structured = sorted(assignments + announcements + calendar, key=lambda item: item.get("date") or "")
    return {
        "structured": structured,
        "analysis": analysis,
        "data_sources": {
            "assignments": {"count": len(assignments)},
            "calendar_events": {"count": len(calendar)},
            "announcements": {"count": announcement_count},
            "ai_analysis": {"available": analysis is not None},
        },
    }


def get_analysis_from_db(
    conn: sqlite3.Connection,
    course_id: int,
    kind: str = "course_overview",
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT content_json FROM analyses WHERE course_id = ? AND kind = ?",
        (course_id, kind),
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["content_json"])
    except json.JSONDecodeError:
        return None


def _parse_raw_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]
