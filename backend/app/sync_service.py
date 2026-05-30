from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx

from .backup_service import BackupService
from .canvas_client import CanvasReadOnlyClient
from .db import Database, utc_now
from .extraction_service import ExtractionService


class SyncCancelled(Exception):
    """Raised when the user requests an orderly sync stop."""


class SyncService:
    def __init__(
        self,
        db: Database,
        canvas: CanvasReadOnlyClient,
        backup: BackupService,
        extractor: ExtractionService,
        is_cancelled: Callable[[], bool] | None = None,
    ):
        self.db = db
        self.canvas = canvas
        self.backup = backup
        self.extractor = extractor
        self.is_cancelled = is_cancelled or (lambda: False)

    def _check_cancelled(self) -> None:
        if self.is_cancelled():
            raise SyncCancelled("Sync interrupted by user.")

    async def sync_all(
        self,
        run_id: int,
        *,
        course_id: int | None = None,
        sync_files: bool = True,
        download_files: bool = False,
    ) -> dict[str, Any]:
        target_course_id = course_id
        counts: dict[str, Any] = {
            "courses": 0,
            "announcements": 0,
            "assignments": 0,
            "calendar_events": 0,
            "pages": 0,
            "people": 0,
            "files": 0,
            "downloaded": 0,
            "extracted": 0,
            "failed": 0,
            "skipped": 0,
            "updated": 0,
            "unchanged": 0,
        }

        def report_progress(
            *,
            percent: int,
            stage: str,
            current: int = 0,
            total: int = 0,
            course: str | None = None,
            file: str | None = None,
            phase: str = "metadata",
            status: str = "running",
        ) -> None:
            counts["progress"] = {
                "percent": max(0, min(100, percent)),
                "stage": stage,
                "current": current,
                "total": total,
                "course": course,
                "file": file,
                "phase": phase,
                "status": status,
            }
            self.db.update_sync_run_counts(run_id, counts, message=stage)

        try:
            self._check_cancelled()
            report_progress(percent=2, stage="Connecting to Canvas")
            self.db.add_event(
                category="sync",
                action="sync_started",
                status="running",
                title="Metadata sync started",
                course_id=target_course_id,
                metadata={"sync_files": sync_files, "download_files": download_files},
            )
            courses = await self.canvas.paginate(
                "/api/v1/courses",
                params={
                    "enrollment_state": "active",
                    "per_page": "100",
                    "include[]": ["term"],
                },
            )
            if course_id is not None:
                courses = [course for course in courses if int(course["id"]) == course_id]
            total_courses = len(courses)
            steps_per_course = 6 if sync_files else 5
            metadata_steps = max(total_courses * steps_per_course, 1)
            completed_steps = 0
            report_progress(percent=8, stage=f"Fetched {total_courses} courses", total=total_courses)

            def complete_step(stage: str, course_index: int, course_name: str) -> None:
                nonlocal completed_steps
                completed_steps += 1
                percent = 8 + int((completed_steps / metadata_steps) * 62)
                report_progress(
                    percent=percent,
                    stage=stage,
                    current=course_index,
                    total=total_courses,
                    course=course_name,
                )

            for course_index, course in enumerate(courses, start=1):
                self._check_cancelled()
                course_id = int(course["id"])
                course_name = course.get("name") or course.get("course_code") or f"course_{course_id}"
                if self._upsert_course(course):
                    counts["updated"] += 1
                    course_outcome = "updated"
                else:
                    counts["unchanged"] += 1
                    course_outcome = "unchanged"
                counts["courses"] += 1
                self.db.add_event(
                    category="sync",
                    action="course_synced",
                    status="success",
                    title="Course shell synced",
                    course_id=course_id,
                    course_name=course_name,
                    item_id=course_id,
                    item_name=course_name,
                    metadata={"outcome": course_outcome},
                )
                complete_step("Synced course shell", course_index, course_name)

                self._merge_sync_delta(counts, "announcements", await self._sync_announcements(course_id))
                complete_step("Synced announcements", course_index, course_name)
                self._merge_sync_delta(counts, "assignments", await self._sync_assignments(course_id))
                complete_step("Synced assignments", course_index, course_name)
                self._merge_sync_delta(counts, "calendar_events", await self._sync_calendar_events(course_id))
                complete_step("Synced calendar events", course_index, course_name)
                self._merge_sync_delta(counts, "pages", await self._sync_pages(course_id))
                complete_step("Synced pages", course_index, course_name)
                self._merge_sync_delta(counts, "people", await self._sync_people(course_id))
                if sync_files:
                    self._merge_sync_delta(counts, "files", await self._sync_files(course_id))
                    complete_step("Synced people and file index", course_index, course_name)
                else:
                    complete_step("Synced people", course_index, course_name)

            if download_files:
                report_progress(
                    percent=72,
                    stage="Metadata sync completed; downloading course files in background",
                    current=0,
                    total=total_courses,
                    phase="download",
                )
                download_total = max(total_courses, 1)
                for course_index, course in enumerate(courses, start=1):
                    self._check_cancelled()
                    course_id = int(course["id"])
                    course_name = course.get("name") or course.get("course_code") or f"course_{course_id}"

                    def report_file_progress(done: int, total: int, file_name: str | None) -> None:
                        file_fraction = (done / total) if total else 0
                        overall_fraction = ((course_index - 1) + file_fraction) / download_total
                        report_progress(
                            percent=72 + int(overall_fraction * 26),
                            stage="Background courseware download",
                            current=done,
                            total=total,
                            course=course_name,
                            file=file_name,
                            phase="download",
                        )

                    backup_counts = await self.backup.backup_course_files(
                        course_id,
                        check_cancelled=self._check_cancelled,
                        on_progress=report_file_progress,
                    )
                    counts["downloaded"] += backup_counts["downloaded"]
                    counts["skipped"] += backup_counts["skipped"]
                    counts["failed"] += backup_counts["failed"]
                    self._check_cancelled()
                    extraction_counts = await self.extractor.extract_course(course_id)
                    counts["extracted"] += extraction_counts["extracted"] + extraction_counts["partial"]
                    counts["failed"] += extraction_counts["failed"]
                    report_progress(
                        percent=72 + int((course_index / download_total) * 26),
                        stage="Indexed downloaded course files",
                        current=course_index,
                        total=total_courses,
                        course=course_name,
                        phase="download",
                    )

            report_progress(
                percent=100,
                stage="Sync completed",
                current=total_courses,
                total=total_courses,
                status="succeeded",
            )
            self.db.finish_sync_run(run_id, "succeeded", counts=counts)
            self.db.add_event(
                category="sync",
                action="sync_completed",
                status="success",
                title="Metadata sync completed",
                course_id=target_course_id,
                metadata={key: value for key, value in counts.items() if key != "progress"},
            )
            return counts
        except SyncCancelled as exc:
            progress = counts.get("progress") if isinstance(counts.get("progress"), dict) else {}
            counts["progress"] = {
                **progress,
                "stage": "Sync interrupted",
                "status": "cancelled",
            }
            self.db.finish_sync_run(run_id, "cancelled", message=str(exc), counts=counts)
            self.db.add_event(
                category="sync",
                action="sync_cancelled",
                status="warning",
                title="Metadata sync cancelled",
                course_id=target_course_id,
                message=str(exc),
                metadata={key: value for key, value in counts.items() if key != "progress"},
            )
            return counts
        except asyncio.CancelledError:
            progress = counts.get("progress") if isinstance(counts.get("progress"), dict) else {}
            counts["progress"] = {
                **progress,
                "stage": "Sync interrupted",
                "status": "cancelled",
            }
            self.db.finish_sync_run(run_id, "cancelled", message="Sync task was cancelled.", counts=counts)
            self.db.add_event(
                category="sync",
                action="sync_cancelled",
                status="warning",
                title="Metadata sync cancelled",
                course_id=target_course_id,
                message="Sync task was cancelled.",
                metadata={key: value for key, value in counts.items() if key != "progress"},
            )
            return counts
        except Exception as exc:
            progress = counts.get("progress") if isinstance(counts.get("progress"), dict) else {}
            counts["progress"] = {
                **progress,
                "stage": f"Sync failed: {exc.__class__.__name__}",
                "status": "failed",
            }
            self.db.finish_sync_run(
                run_id,
                "failed",
                message=f"{exc.__class__.__name__}: {exc}",
                counts=counts,
            )
            self.db.add_event(
                category="sync",
                action="sync_failed",
                status="failed",
                title="Metadata sync failed",
                course_id=target_course_id,
                message=f"{exc.__class__.__name__}: {exc}",
                metadata={key: value for key, value in counts.items() if key != "progress"},
            )
            raise

    async def sync_course_non_file(self, run_id: int, course_id: int) -> dict[str, Any]:
        return await self.sync_all(run_id, course_id=course_id, sync_files=False, download_files=False)

    async def sync_course_files(self, course_id: int) -> dict[str, Any]:
        self._check_cancelled()
        index_counts = await self._sync_files(course_id)
        self._check_cancelled()
        backup_counts = await self.backup.backup_course_files(course_id, check_cancelled=self._check_cancelled)
        extraction_counts = await self.extractor.extract_course(course_id)
        return {
            "status": "completed",
            "index": index_counts,
            "backup": backup_counts,
            "extraction": extraction_counts,
        }

    def _merge_sync_delta(self, counts: dict[str, Any], key: str, delta: dict[str, int]) -> None:
        counts[key] += delta["seen"]
        counts["updated"] += delta["updated"]
        counts["unchanged"] += delta["unchanged"]

    def _canonical_raw_json(self, item: dict) -> str:
        return json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _raw_json_matches(self, stored_raw_json: str | None, next_raw_json: str) -> bool:
        if not stored_raw_json:
            return False
        try:
            return json.loads(stored_raw_json) == json.loads(next_raw_json)
        except json.JSONDecodeError:
            return stored_raw_json == next_raw_json

    def _raw_json_is_unchanged(
        self,
        conn,
        query: str,
        params: tuple[Any, ...],
        next_raw_json: str,
    ) -> bool:
        row = conn.execute(query, params).fetchone()
        return bool(row and self._raw_json_matches(row["raw_json"], next_raw_json))

    def _upsert_course(self, course: dict) -> bool:
        term = course.get("term") or {}
        now = utc_now()
        raw_json = self._canonical_raw_json(course)
        with self.db.connect() as conn:
            if self._raw_json_is_unchanged(conn, "SELECT raw_json FROM courses WHERE id = ?", (course["id"],), raw_json):
                return False
            conn.execute(
                """
                INSERT INTO courses(id, name, course_code, workflow_state, term_name, raw_json, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    course_code=excluded.course_code,
                    workflow_state=excluded.workflow_state,
                    term_name=excluded.term_name,
                    raw_json=excluded.raw_json,
                    synced_at=excluded.synced_at
                """,
                (
                    course["id"],
                    course.get("name") or "Untitled course",
                    course.get("course_code"),
                    course.get("workflow_state"),
                    term.get("name"),
                    raw_json,
                    now,
                ),
            )
        return True

    async def _sync_announcements(self, course_id: int) -> dict[str, int]:
        course_name = self._course_label(course_id)
        end_date = (datetime.now(timezone.utc) + timedelta(days=3650)).strftime("%Y-%m-%dT%H:%M:%SZ")
        items = await self._optional_paginate(
            "/api/v1/announcements",
            params={
                "context_codes[]": [f"course_{course_id}"],
                "per_page": "100",
                "start_date": "1970-01-01T00:00:00Z",
                "end_date": end_date,
                "active_only": "false",
                "latest_only": "false",
            },
        )
        now = utc_now()
        updated = 0
        unchanged = 0
        events: list[dict[str, Any]] = []
        with self.db.connect() as conn:
            for item in items:
                raw_json = self._canonical_raw_json(item)
                if self._raw_json_is_unchanged(conn, "SELECT raw_json FROM announcements WHERE id = ?", (item["id"],), raw_json):
                    unchanged += 1
                    events.append(
                        {
                            "category": "announcement",
                            "action": "announcement_synced",
                            "status": "success",
                            "title": "Announcement unchanged",
                            "course_id": course_id,
                            "course_name": course_name,
                            "item_id": item["id"],
                            "item_name": item.get("title") or "Untitled announcement",
                            "metadata": {"outcome": "unchanged", "posted_at": item.get("posted_at")},
                        }
                    )
                    continue
                conn.execute(
                    """
                    INSERT INTO announcements(id, course_id, title, message, posted_at, author_name, raw_json, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title=excluded.title,
                        message=excluded.message,
                        posted_at=excluded.posted_at,
                        author_name=excluded.author_name,
                        raw_json=excluded.raw_json,
                        synced_at=excluded.synced_at
                    """,
                    (
                        item["id"],
                        course_id,
                        item.get("title") or "Untitled announcement",
                        item.get("message"),
                        item.get("posted_at"),
                        item.get("user_name"),
                        raw_json,
                        now,
                    ),
                )
                updated += 1
                events.append(
                    {
                        "category": "announcement",
                        "action": "announcement_synced",
                        "status": "success",
                        "title": "Announcement updated",
                        "course_id": course_id,
                        "course_name": course_name,
                        "item_id": item["id"],
                        "item_name": item.get("title") or "Untitled announcement",
                        "metadata": {"outcome": "updated", "posted_at": item.get("posted_at")},
                    }
                )
        for event in events:
            self.db.add_event(**event)
        return {"seen": len(items), "updated": updated, "unchanged": unchanged}

    async def _sync_assignments(self, course_id: int) -> dict[str, int]:
        course_name = self._course_label(course_id)
        direct_items = await self._optional_paginate(
            f"/api/v1/courses/{course_id}/assignments",
            params={
                "per_page": "100",
                "order_by": "due_at",
                "include[]": ["all_dates", "overrides", "submission"],
                "override_assignment_dates": "true",
            },
        )
        assignment_groups = await self._optional_paginate(
            f"/api/v1/courses/{course_id}/assignment_groups",
            params={
                "per_page": "100",
                "include[]": ["assignments", "all_dates", "overrides", "submission"],
                "override_assignment_dates": "true",
            },
        )
        items_by_id: dict[int, dict[str, Any]] = {}
        for item in direct_items:
            items_by_id[int(item["id"])] = item
        for group in assignment_groups:
            for item in group.get("assignments") or []:
                merged = {
                    **item,
                    "assignment_group_id": item.get("assignment_group_id") or group.get("id"),
                    "assignment_group_name": group.get("name"),
                    **items_by_id.get(int(item["id"]), {}),
                }
                items_by_id[int(item["id"])] = merged
        items = list(items_by_id.values())
        now = utc_now()
        updated = 0
        unchanged = 0
        events: list[dict[str, Any]] = []
        with self.db.connect() as conn:
            for item in items:
                raw_json = self._canonical_raw_json(item)
                if self._raw_json_is_unchanged(conn, "SELECT raw_json FROM assignments WHERE id = ?", (item["id"],), raw_json):
                    unchanged += 1
                    events.append(
                        {
                            "category": "assignment",
                            "action": "assignment_synced",
                            "status": "success",
                            "title": "Assignment unchanged",
                            "course_id": course_id,
                            "course_name": course_name,
                            "item_id": item["id"],
                            "item_name": item.get("name") or "Untitled assignment",
                            "metadata": {"outcome": "unchanged", "due_at": item.get("due_at")},
                        }
                    )
                    continue
                conn.execute(
                    """
                    INSERT INTO assignments(id, course_id, name, due_at, unlock_at, lock_at, workflow_state, points_possible, raw_json, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,
                        due_at=excluded.due_at,
                        unlock_at=excluded.unlock_at,
                        lock_at=excluded.lock_at,
                        workflow_state=excluded.workflow_state,
                        points_possible=excluded.points_possible,
                        raw_json=excluded.raw_json,
                        synced_at=excluded.synced_at
                    """,
                    (
                        item["id"],
                        course_id,
                        item.get("name") or "Untitled assignment",
                        item.get("due_at"),
                        item.get("unlock_at"),
                        item.get("lock_at"),
                        item.get("workflow_state"),
                        item.get("points_possible"),
                        raw_json,
                        now,
                    ),
                )
                updated += 1
                events.append(
                    {
                        "category": "assignment",
                        "action": "assignment_synced",
                        "status": "success",
                        "title": "Assignment updated",
                        "course_id": course_id,
                        "course_name": course_name,
                        "item_id": item["id"],
                        "item_name": item.get("name") or "Untitled assignment",
                        "metadata": {"outcome": "updated", "due_at": item.get("due_at")},
                    }
                )
        for event in events:
            self.db.add_event(**event)
        return {"seen": len(items), "updated": updated, "unchanged": unchanged}

    async def _sync_calendar_events(self, course_id: int) -> dict[str, int]:
        items = await self._optional_paginate(
            "/api/v1/calendar_events",
            params={"context_codes[]": [f"course_{course_id}"], "per_page": "100", "all_events": "true"},
        )
        now = utc_now()
        updated = 0
        unchanged = 0
        with self.db.connect() as conn:
            for item in items:
                raw_json = self._canonical_raw_json(item)
                if self._raw_json_is_unchanged(conn, "SELECT raw_json FROM calendar_events WHERE id = ?", (item["id"],), raw_json):
                    unchanged += 1
                    continue
                conn.execute(
                    """
                    INSERT INTO calendar_events(id, course_id, title, start_at, end_at, event_type, raw_json, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title=excluded.title,
                        start_at=excluded.start_at,
                        end_at=excluded.end_at,
                        event_type=excluded.event_type,
                        raw_json=excluded.raw_json,
                        synced_at=excluded.synced_at
                    """,
                    (
                        item["id"],
                        course_id,
                        item.get("title") or "Untitled event",
                        item.get("start_at"),
                        item.get("end_at"),
                        item.get("type"),
                        raw_json,
                        now,
                    ),
                )
                updated += 1
        return {"seen": len(items), "updated": updated, "unchanged": unchanged}

    async def _sync_pages(self, course_id: int) -> dict[str, int]:
        pages = await self._optional_paginate(
            f"/api/v1/courses/{course_id}/pages",
            params={"per_page": "100"},
        )
        now = utc_now()
        details: list[tuple[dict, dict, str]] = []
        for page in pages:
            detail = page
            page_url = page.get("url")
            if page_url:
                try:
                    detail = await self.canvas.get_json(
                        f"/api/v1/courses/{course_id}/pages/{quote(page_url, safe='')}"
                    )
                except httpx.HTTPStatusError:
                    detail = page
            details.append((page, detail, page_url or str(page.get("page_id") or page.get("id"))))

        updated = 0
        unchanged = 0
        with self.db.connect() as conn:
            for page, detail, page_url in details:
                raw_json = self._canonical_raw_json(detail)
                if self._raw_json_is_unchanged(
                    conn,
                    "SELECT raw_json FROM pages WHERE course_id = ? AND page_url = ?",
                    (course_id, page_url or str(page.get("page_id") or page.get("id"))),
                    raw_json,
                ):
                    unchanged += 1
                    continue
                conn.execute(
                    """
                    INSERT INTO pages(course_id, page_url, page_id, title, body, updated_at, published, raw_json, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(course_id, page_url) DO UPDATE SET
                        page_id=excluded.page_id,
                        title=excluded.title,
                        body=excluded.body,
                        updated_at=excluded.updated_at,
                        published=excluded.published,
                        raw_json=excluded.raw_json,
                        synced_at=excluded.synced_at
                    """,
                    (
                        course_id,
                        page_url or str(page.get("page_id") or page.get("id")),
                        detail.get("page_id") or page.get("page_id"),
                        detail.get("title") or page.get("title") or "Untitled page",
                        detail.get("body"),
                        detail.get("updated_at") or page.get("updated_at"),
                        1 if detail.get("published", page.get("published")) else 0,
                        raw_json,
                        now,
                    ),
                )
                updated += 1
        return {"seen": len(details), "updated": updated, "unchanged": unchanged}

    async def _sync_people(self, course_id: int) -> dict[str, int]:
        items = await self._optional_paginate(
            f"/api/v1/courses/{course_id}/users",
            params={
                "per_page": "100",
                "include[]": ["email", "enrollments"],
                "enrollment_state[]": ["active"],
            },
        )
        now = utc_now()
        updated = 0
        unchanged = 0
        with self.db.connect() as conn:
            for item in items:
                raw_json = self._canonical_raw_json(item)
                if self._raw_json_is_unchanged(
                    conn,
                    "SELECT raw_json FROM course_people WHERE course_id = ? AND user_id = ?",
                    (course_id, item["id"]),
                    raw_json,
                ):
                    unchanged += 1
                    continue
                enrollments = item.get("enrollments") or []
                role = None
                last_activity_at = item.get("last_activity_at")
                if enrollments:
                    enrollment = enrollments[0] or {}
                    role = enrollment.get("role") or enrollment.get("type")
                    last_activity_at = last_activity_at or enrollment.get("last_activity_at")
                conn.execute(
                    """
                    INSERT INTO course_people(
                        course_id, user_id, name, sortable_name, email, role,
                        last_activity_at, raw_json, synced_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(course_id, user_id) DO UPDATE SET
                        name=excluded.name,
                        sortable_name=excluded.sortable_name,
                        email=excluded.email,
                        role=excluded.role,
                        last_activity_at=excluded.last_activity_at,
                        raw_json=excluded.raw_json,
                        synced_at=excluded.synced_at
                    """,
                    (
                        course_id,
                        item["id"],
                        item.get("name") or item.get("short_name") or "Unnamed user",
                        item.get("sortable_name"),
                        item.get("email"),
                        role,
                        last_activity_at,
                        raw_json,
                        now,
                    ),
                )
                updated += 1
        return {"seen": len(items), "updated": updated, "unchanged": unchanged}

    async def _sync_files(self, course_id: int) -> dict[str, int]:
        course_name = self._course_label(course_id)
        files = await self._optional_paginate(
            f"/api/v1/courses/{course_id}/files",
            params={"per_page": "100", "include[]": ["usage_rights"]},
        )
        now = utc_now()
        updated = 0
        unchanged = 0
        events: list[dict[str, Any]] = []
        indexed_files: list[tuple[dict[str, Any], str]] = []
        for item in files:
            redacted_item = self._redact_file_metadata(item)
            try:
                redacted_item["canvas_folder_path"] = "/".join(
                    await self.backup.canvas_folder_segments(redacted_item)
                )
            except httpx.HTTPError:
                redacted_item["canvas_folder_path"] = ""
            indexed_files.append((item, self._canonical_raw_json(redacted_item)))

        with self.db.connect() as conn:
            for item, raw_json in indexed_files:
                display_name = item.get("display_name") or item.get("filename") or f"file-{item['id']}"
                if self._raw_json_is_unchanged(conn, "SELECT raw_json FROM files WHERE id = ?", (item["id"],), raw_json):
                    unchanged += 1
                    events.append(
                        {
                            "category": "file",
                            "action": "file_indexed",
                            "status": "success",
                            "title": "File index unchanged",
                            "course_id": course_id,
                            "course_name": course_name,
                            "item_id": item["id"],
                            "item_name": display_name,
                            "metadata": {"outcome": "unchanged", "updated_at": item.get("updated_at")},
                        }
                    )
                    continue
                conn.execute(
                    """
                    INSERT INTO files(id, course_id, display_name, filename, content_type, size, updated_at, canvas_url, raw_json, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        course_id=excluded.course_id,
                        display_name=excluded.display_name,
                        filename=excluded.filename,
                        content_type=excluded.content_type,
                        size=excluded.size,
                        updated_at=excluded.updated_at,
                        canvas_url=excluded.canvas_url,
                        raw_json=excluded.raw_json,
                        synced_at=excluded.synced_at
                    """,
                    (
                        item["id"],
                        course_id,
                        display_name,
                        item.get("filename") or display_name,
                        item.get("content-type") or item.get("content_type"),
                        item.get("size"),
                        item.get("updated_at"),
                        None,
                        raw_json,
                        now,
                    ),
                )
                updated += 1
                events.append(
                    {
                        "category": "file",
                        "action": "file_indexed",
                        "status": "success",
                        "title": "File index updated",
                        "course_id": course_id,
                        "course_name": course_name,
                        "item_id": item["id"],
                        "item_name": display_name,
                        "metadata": {"outcome": "updated", "updated_at": item.get("updated_at")},
                    }
                )
        for event in events:
            self.db.add_event(**event)
        return {"seen": len(files), "updated": updated, "unchanged": unchanged}

    def _course_label(self, course_id: int) -> str:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT name, course_code FROM courses WHERE id = ?",
                (course_id,),
            ).fetchone()
        if not row:
            return f"course_{course_id}"
        return row["course_code"] or row["name"] or f"course_{course_id}"

    def _redact_file_metadata(self, item: dict) -> dict:
        redacted = dict(item)
        for key in ("url", "thumbnail_url", "preview_url"):
            if key in redacted:
                redacted[key] = None
        return redacted

    async def _optional_paginate(self, path: str, params: dict) -> list:
        try:
            return await self.canvas.paginate(path, params=params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403, 404}:
                return []
            raise
