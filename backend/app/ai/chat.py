"""Canvas chat-agent domain logic: system prompt, agent builders, and tools.

Kept out of the API layer (``api/ai.py``) so the HTTP router stays thin, and out
of ``ai/__init__.py`` so importing it does not create a cycle with ``runtime``.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from ..backup_service import BackupService
from ..db import rows_to_dicts
from ..notification_service import build_notification_agent_tools
from ..runtime import (
    make_canvas_client,
    make_extractor,
    make_notification_service,
    project_root,
    state,
)
from .agent import AgentConfig, AgentTool, OpenAICompatAgent, SkillRegistry, build_shell_agent_tools

AGENT_SYSTEM_PROMPT = (
    "You are the Canvas_helper agent. Answer the user's request directly, working only from locally synced "
    "Canvas data through the provided tools. Never request Canvas credentials or raw Canvas API access.\n\n"
    "How to find information inside a Canvas course (reason through this before answering):\n"
    "- Course schedule / syllabus / exam plan is rarely a single field. Cross-check three sources: (1) the course "
    "home page and syllabus/overview pages, (2) announcements for updates and exam notices, and (3) the first "
    "lecture's slides. Use search_files for names like 'Lecture 1', 'Week 1', 'Intro', 'Syllabus', 'Outline', "
    "'\u8bfe\u7a0b\u5927\u7eb2', '\u6559\u5b66\u65e5\u5386', '\u8bfe\u7a0b\u4ecb\u7ecd', then read_file them \u2014 the first lecture deck almost always lists "
    "the full schedule, grading, and exam/test arrangement.\n"
    "- Course content / topics: read the lecture courseware (slides, pdf, pptx, docx) with search_files then "
    "read_file. Judge by the file's text, not its title alone.\n"
    "- Deadlines, dated items, or building a timetable (including across all courses): call list_schedule to "
    "enumerate assignments and calendar events, and read each item's details to tell in-person from online.\n"
    "- Keyword lookups across announcements, assignments, pages, and extracted file text: call "
    "search_course_materials; pass the course code (e.g. CS3334) as course when the user names one.\n\n"
    "If a file is found but not yet downloaded, call download_file before read_file. Use local/shell tools when "
    "useful; filesystem writes are allowed only inside the project sandbox, outside paths are read-only. Use "
    "notification tools only for explicit notification or reminder requests."
)


def build_agent_chat_context(course_id: int | None = None) -> dict[str, Any]:
    with state().db.connect() as conn:
        courses = rows_to_dicts(
            conn.execute(
                """
                SELECT c.id, c.name, c.course_code, c.term_name,
                       (SELECT COUNT(*) FROM assignments a WHERE a.course_id = c.id) AS assignment_count,
                       (SELECT COUNT(*) FROM files f WHERE f.course_id = c.id) AS file_count
                FROM courses c
                ORDER BY c.term_name DESC, c.name
                LIMIT 50
                """
            ).fetchall()
        )
        selected: dict[str, Any] | None = None
        if course_id is not None:
            row = conn.execute(
                """
                SELECT id, name, course_code, term_name
                FROM courses
                WHERE id = ?
                """,
                (course_id,),
            ).fetchone()
            if row:
                assignments = rows_to_dicts(
                    conn.execute(
                        """
                        SELECT name, due_at, unlock_at, lock_at, points_possible
                        FROM assignments
                        WHERE course_id = ?
                        ORDER BY COALESCE(due_at, unlock_at, lock_at), name
                        LIMIT 30
                        """,
                        (course_id,),
                    ).fetchall()
                )
                selected = {
                    "course": dict(row),
                    "assignments": assignments,
                    "analysis": state().db.get_analysis(course_id),
                }
    return {"courses": courses, "selected_course": selected}


def build_agent(ai_settings: dict[str, Any]) -> OpenAICompatAgent:
    return OpenAICompatAgent(
        AgentConfig(
            base_url=ai_settings["base_url"],
            api_key=ai_settings["api_key"],
            model=ai_settings["model"],
            reasoning_effort=ai_settings["reasoning_effort"],
            max_tool_rounds=6,
        ),
        skills=SkillRegistry.from_text(ai_settings["skills"]),
    )


def build_agent_tools():
    shell_tools = build_shell_agent_tools(project_root()) if state().settings.agent_shell_tools_enabled else []
    return (
        [_course_search_tool(), _file_search_tool(), _file_read_tool(), _file_download_tool(), _schedule_tool()]
        + shell_tools
        + build_notification_agent_tools(make_notification_service())
    )


def _course_search_tool() -> AgentTool:
    return AgentTool(
        name="search_course_materials",
        description="Search synced course announcements, assignments, pages, and extracted file text. Filter by course code or name with 'course'.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords to search for, e.g. 'final exam'."},
                "course": {"type": "string", "description": "Optional course code or name filter, e.g. CS3334."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
            },
            "required": ["query"],
        },
        handler=_search_course_materials,
    )


def _search_course_materials(args: dict[str, Any]) -> list[dict[str, Any]]:
    query = str(args.get("query") or "").strip()
    if not query:
        return []
    needle = query.lower()
    course_filter = str(args.get("course") or "").strip().lower()
    limit = max(1, min(int(args.get("limit") or 8) if str(args.get("limit") or "8").isdigit() else 8, 20))
    matches: list[dict[str, Any]] = []
    with state().db.connect() as conn:
        courses = rows_to_dicts(conn.execute("SELECT id, name, course_code FROM courses").fetchall())
        course_ids = [
            c["id"]
            for c in courses
            if not course_filter
            or course_filter in (c.get("course_code") or "").lower()
            or course_filter in (c.get("name") or "").lower()
        ]
        labels = {c["id"]: (c.get("course_code") or c.get("name") or f"course_{c['id']}") for c in courses}
        if not course_ids:
            return []
        placeholders = ",".join("?" for _ in course_ids)
        queries = [
            ("announcement", f"SELECT course_id, title, message AS body FROM announcements WHERE course_id IN ({placeholders})"),
            ("assignment", f"SELECT course_id, name AS title, COALESCE(name,'') || ' due ' || COALESCE(due_at,'') AS body FROM assignments WHERE course_id IN ({placeholders})"),
            ("page", f"SELECT course_id, title, COALESCE(body,'') AS body FROM pages WHERE course_id IN ({placeholders})"),
        ]
        for source, sql in queries:
            for row in rows_to_dicts(conn.execute(sql, course_ids).fetchall()):
                blob = f"{row.get('title') or ''}\n{row.get('body') or ''}"
                if needle in blob.lower():
                    matches.append(_match(labels, row["course_id"], source, row.get("title") or "", blob, needle))
                    if len(matches) >= limit:
                        return matches
        files = rows_to_dicts(
            conn.execute(
                f"SELECT course_id, display_name, extracted_text_path FROM files WHERE course_id IN ({placeholders})",
                course_ids,
            ).fetchall()
        )
    for row in files:
        name = row.get("display_name") or ""
        text = ""
        path = row.get("extracted_text_path")
        if path and Path(path).exists():
            try:
                text = Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
        blob = f"{name}\n{text}"
        if needle in blob.lower():
            matches.append(_match(labels, row["course_id"], "file", name, blob, needle))
            if len(matches) >= limit:
                break
    return matches


def _match(labels: dict[int, str], course_id: int, source: str, title: str, blob: str, needle: str) -> dict[str, Any]:
    idx = blob.lower().find(needle)
    start = max(0, idx - 200)
    snippet = blob[start : start + 600].strip()
    return {"course": labels.get(course_id, course_id), "source": source, "title": title, "snippet": snippet}


def _file_search_tool() -> AgentTool:
    return AgentTool(
        name="search_files",
        description="Search the synced course file index by name. Returns file_id, course, type, and download/extraction status.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to match in the file name."},
                "course": {"type": "string", "description": "Optional course code or name filter."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 30, "default": 12},
            },
            "required": ["query"],
        },
        handler=_search_files,
    )


def _file_read_tool() -> AgentTool:
    return AgentTool(
        name="read_file",
        description="Read the extracted text of a synced course file (pdf, docx, pptx, etc.) by file_id. Downloads-then-extracts on demand if needed.",
        parameters={
            "type": "object",
            "properties": {
                "file_id": {"type": "integer"},
                "limit": {"type": "integer", "minimum": 200, "maximum": 16000, "default": 8000},
            },
            "required": ["file_id"],
        },
        handler=_read_file,
    )


def _file_download_tool() -> AgentTool:
    return AgentTool(
        name="download_file",
        description="Download a course file from Canvas to the local cache by file_id and extract its text.",
        parameters={"type": "object", "properties": {"file_id": {"type": "integer"}}, "required": ["file_id"]},
        handler=lambda args: _download_file(int(args.get("file_id"))),
    )


def _schedule_tool() -> AgentTool:
    return AgentTool(
        name="list_schedule",
        description=(
            "Enumerate dated items (assignments and calendar events) across synced courses to build schedules or "
            "find exam/test dates, even across all courses. Optionally filter by course code or name. Each item "
            "includes course, title, its dates, and a details snippet (description/location) useful to tell "
            "in-person tests from online ones."
        ),
        parameters={
            "type": "object",
            "properties": {
                "course": {"type": "string", "description": "Optional course code or name filter."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 100},
            },
        },
        handler=_list_schedule,
    )


def _schedule_detail(raw_json: str | None, *keys: str) -> str:
    try:
        data = json.loads(raw_json) if raw_json else {}
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    parts = []
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()
            if text:
                parts.append(f"{key}: {text[:400]}")
    return " | ".join(parts)


def _list_schedule(args: dict[str, Any]) -> list[dict[str, Any]]:
    course_filter = str(args.get("course") or "").strip().lower()
    limit = max(1, min(int(args["limit"]) if str(args.get("limit") or "").isdigit() else 100, 200))
    items: list[dict[str, Any]] = []
    with state().db.connect() as conn:
        courses = rows_to_dicts(conn.execute("SELECT id, name, course_code FROM courses").fetchall())
        course_ids = [
            c["id"]
            for c in courses
            if not course_filter
            or course_filter in (c.get("course_code") or "").lower()
            or course_filter in (c.get("name") or "").lower()
        ]
        labels = {c["id"]: (c.get("course_code") or c.get("name") or f"course_{c['id']}") for c in courses}
        if not course_ids:
            return []
        placeholders = ",".join("?" for _ in course_ids)
        for row in rows_to_dicts(
            conn.execute(
                f"SELECT course_id, name, due_at, unlock_at, lock_at, raw_json FROM assignments WHERE course_id IN ({placeholders})",
                course_ids,
            ).fetchall()
        ):
            if not (row.get("due_at") or row.get("unlock_at") or row.get("lock_at")):
                continue
            items.append(
                {
                    "course": labels.get(row["course_id"]),
                    "type": "assignment",
                    "title": row.get("name"),
                    "due_at": row.get("due_at"),
                    "unlock_at": row.get("unlock_at"),
                    "lock_at": row.get("lock_at"),
                    "details": _schedule_detail(row.get("raw_json"), "description"),
                }
            )
        for row in rows_to_dicts(
            conn.execute(
                f"SELECT course_id, title, start_at, end_at, raw_json FROM calendar_events WHERE course_id IN ({placeholders})",
                course_ids,
            ).fetchall()
        ):
            items.append(
                {
                    "course": labels.get(row["course_id"]),
                    "type": "calendar_event",
                    "title": row.get("title"),
                    "start_at": row.get("start_at"),
                    "end_at": row.get("end_at"),
                    "details": _schedule_detail(row.get("raw_json"), "location_name", "location_address", "description"),
                }
            )
    items.sort(key=lambda item: item.get("due_at") or item.get("start_at") or item.get("unlock_at") or "")
    return items[:limit]


def _search_files(args: dict[str, Any]) -> list[dict[str, Any]]:
    needle = str(args.get("query") or "").strip().lower()
    if not needle:
        return []
    course_filter = str(args.get("course") or "").strip().lower()
    limit = max(1, min(int(args["limit"]) if str(args.get("limit") or "").isdigit() else 12, 30))
    with state().db.connect() as conn:
        rows = rows_to_dicts(
            conn.execute(
                """
                SELECT f.id, f.display_name, f.content_type, f.backup_status, f.extraction_status,
                       c.course_code, c.name AS course_name
                FROM files f JOIN courses c ON c.id = f.course_id
                ORDER BY f.display_name
                """
            ).fetchall()
        )
    results = []
    for row in rows:
        label = f"{row.get('course_code') or ''} {row.get('course_name') or ''}".lower()
        if course_filter and course_filter not in label:
            continue
        if needle not in (row.get("display_name") or "").lower():
            continue
        results.append(
            {
                "file_id": row["id"],
                "course": row.get("course_code") or row.get("course_name"),
                "display_name": row.get("display_name"),
                "type": row.get("content_type"),
                "downloaded": row.get("backup_status") == "downloaded",
                "extracted": row.get("extraction_status") == "extracted",
            }
        )
        if len(results) >= limit:
            break
    return results


def _file_row(file_id: int) -> dict[str, Any]:
    with state().db.connect() as conn:
        row = conn.execute(
            "SELECT id, course_id, display_name, content_type, local_path, extracted_text_path FROM files WHERE id = ?",
            (file_id,),
        ).fetchone()
    if not row:
        raise ValueError(f"File not found: {file_id}")
    return dict(row)


def _read_file(args: dict[str, Any]) -> dict[str, Any]:
    file_id = int(args.get("file_id"))
    limit = max(200, min(int(args["limit"]) if str(args.get("limit") or "").isdigit() else 8000, 16000))
    row = _file_row(file_id)
    text_path = row.get("extracted_text_path")
    if text_path and Path(text_path).exists():
        text = Path(text_path).read_text(encoding="utf-8", errors="replace")
    elif row.get("local_path") and Path(row["local_path"]).exists():
        text, _status, _warning = make_extractor().extract_file(Path(row["local_path"]), row.get("content_type"))
    else:
        return {"file_id": file_id, "display_name": row.get("display_name"), "text": "", "note": "Not downloaded yet; call download_file first."}
    if len(text) > limit:
        text = text[:limit] + "\n[truncated]"
    return {"file_id": file_id, "display_name": row.get("display_name"), "text": text}


def _download_file(file_id: int) -> dict[str, Any]:
    row = _file_row(file_id)
    course_id = row["course_id"]

    async def run() -> dict[str, int]:
        async with make_canvas_client() as canvas:
            backup = BackupService(state().db, canvas, state().settings.data_dir)
            counts = await backup.backup_files(course_id, file_ids=[file_id])
        await make_extractor().extract_files(course_id, file_ids=[file_id])
        return counts

    counts = asyncio.run(run())
    return {"file_id": file_id, "display_name": row.get("display_name"), "download": counts}
