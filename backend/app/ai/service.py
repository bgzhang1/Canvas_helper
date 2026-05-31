from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .agent import (
    AgentConfig,
    AgentTool,
    OpenAICompatAgent,
    SkillRegistry,
    build_course_agent_input,
    build_course_agent_tools,
    parse_model_json,
)
from ..db import Database, rows_to_dicts, utc_now


@dataclass(frozen=True)
class AIConfig:
    base_url: str | None
    api_key: str | None
    model: str
    reasoning_effort: str = "medium"
    skills: str = ""


class AIAnalysisService:
    """Analyze locally synced course material without access to Canvas credentials."""

    def __init__(self, db: Database, config: AIConfig, agent_tools: list[AgentTool] | None = None):
        self.db = db
        self.config = config
        self.agent_tools = agent_tools or []

    async def analyze_course(self, course_id: int, on_progress: Callable[..., None] | None = None) -> dict[str, Any]:
        self._report(on_progress, percent=3, stage="Preparing AI analysis")
        payload = self._build_course_payload(course_id, on_progress=on_progress)
        if self.config.base_url and self.config.api_key:
            self._report(on_progress, percent=62, stage="Waiting for AI model")
            result = await self._call_openai_compatible(payload)
            model = self.config.model
        else:
            self._report(on_progress, percent=70, stage="Running local analysis")
            result = self._fallback_analysis(payload)
            model = "local-fallback"

        self._report(on_progress, percent=92, stage="Saving AI analysis")
        result.setdefault("generated_at", utc_now())
        result.setdefault("model", model)
        self._save_analysis(course_id, "course_overview", result, model)
        self._report(on_progress, percent=100, stage="AI analysis completed")
        return result

    def get_analysis(self, course_id: int) -> dict[str, Any] | None:
        return self.db.get_analysis(course_id, "course_overview")

    def _build_course_payload(self, course_id: int, on_progress: Callable[..., None] | None = None) -> dict[str, Any]:
        self._report(on_progress, percent=8, stage="Collecting cached course data")
        with self.db.connect() as conn:
            course = conn.execute(
                "SELECT id, name, course_code, term_name FROM courses WHERE id = ?",
                (course_id,),
            ).fetchone()
            if not course:
                raise ValueError(f"Course {course_id} is not synced")
            announcements = rows_to_dicts(
                conn.execute(
                    """
                    SELECT title, message, posted_at, author_name
                    FROM announcements
                    WHERE course_id = ?
                    ORDER BY posted_at DESC
                    LIMIT 40
                    """,
                    (course_id,),
                ).fetchall()
            )
            assignments = rows_to_dicts(
                conn.execute(
                    """
                    SELECT name, due_at, unlock_at, lock_at, points_possible
                    FROM assignments
                    WHERE course_id = ?
                    ORDER BY COALESCE(due_at, unlock_at, lock_at), name
                    """,
                    (course_id,),
                ).fetchall()
            )
            pages = rows_to_dicts(
                conn.execute(
                    """
                    SELECT title, body, updated_at
                    FROM pages
                    WHERE course_id = ?
                    ORDER BY updated_at DESC
                    LIMIT 20
                    """,
                    (course_id,),
                ).fetchall()
            )
            files = rows_to_dicts(
                conn.execute(
                    """
                    SELECT display_name, updated_at, outline_json, extracted_text_path
                    FROM files
                    WHERE course_id = ?
                    ORDER BY updated_at DESC, display_name
                    LIMIT 80
                    """,
                    (course_id,),
                ).fetchall()
            )

        file_summaries = []
        total_files = len(files)
        for index, item in enumerate(files, start=1):
            if total_files:
                percent = 18 + int((index - 1) / total_files * 38)
                self._report(
                    on_progress,
                    percent=percent,
                    stage="Reading extracted files",
                    current=index - 1,
                    total=total_files,
                    file=item["display_name"],
                )
            text = self._read_text_excerpt(item.get("extracted_text_path"))
            outline = []
            if item.get("outline_json"):
                try:
                    outline = json.loads(item["outline_json"])
                except json.JSONDecodeError:
                    outline = []
            file_summaries.append(
                {
                    "display_name": item["display_name"],
                    "updated_at": item["updated_at"],
                    "outline": outline,
                    "text_excerpt": text,
                }
            )
            if total_files:
                percent = 18 + int(index / total_files * 38)
                self._report(
                    on_progress,
                    percent=percent,
                    stage="Reading extracted files",
                    current=index,
                    total=total_files,
                    file=item["display_name"],
                )

        payload = {
            "course": dict(course),
            "announcements": announcements,
            "assignments": assignments,
            "pages": [
                {
                    **page,
                    "body": self._truncate(page.get("body") or "", 4000),
                }
                for page in pages
            ],
            "files": file_summaries,
        }
        payload["agent_workspace_path"] = str(self._prepare_agent_workspace(course_id, payload))
        payload["agent_project_root"] = str(self._project_root())
        return payload

    def _report(self, callback: Callable[..., None] | None, **progress: Any) -> None:
        if callback:
            callback(**progress)

    def _read_text_excerpt(self, value: str | None) -> str:
        if not value:
            return ""
        path = Path(value)
        if not path.exists():
            return ""
        return self._truncate(path.read_text(encoding="utf-8", errors="replace"), 8000)

    def _truncate(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + "\n[truncated]"

    def _prepare_agent_workspace(self, course_id: int, payload: dict[str, Any]) -> Path:
        root = (self.db.path.parent / "agent_workspace").resolve()
        workspace = (root / f"course_{course_id}").resolve()
        try:
            workspace.relative_to(root)
        except ValueError as exc:
            raise ValueError("Agent workspace path escaped the configured data directory") from exc
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.mkdir(parents=True, exist_ok=True)

        self._write_workspace_file(workspace / "course.json", json.dumps(payload["course"], ensure_ascii=False, indent=2))
        self._write_workspace_file(workspace / "assignments.json", json.dumps(payload["assignments"], ensure_ascii=False, indent=2))
        self._write_workspace_collection(
            workspace / "announcements",
            payload.get("announcements") or [],
            lambda item: str(item.get("title") or "announcement"),
            lambda item: "\n".join(
                [
                    f"title: {item.get('title') or ''}",
                    f"posted_at: {item.get('posted_at') or ''}",
                    f"author_name: {item.get('author_name') or ''}",
                    "",
                    str(item.get("message") or ""),
                ]
            ),
        )
        self._write_workspace_collection(
            workspace / "pages",
            payload.get("pages") or [],
            lambda item: str(item.get("title") or "page"),
            lambda item: "\n".join(
                [
                    f"title: {item.get('title') or ''}",
                    f"updated_at: {item.get('updated_at') or ''}",
                    "",
                    str(item.get("body") or ""),
                ]
            ),
        )
        self._write_workspace_collection(
            workspace / "files",
            payload.get("files") or [],
            lambda item: str(item.get("display_name") or "file"),
            lambda item: "\n".join(
                [
                    f"display_name: {item.get('display_name') or ''}",
                    f"updated_at: {item.get('updated_at') or ''}",
                    "outline:",
                    json.dumps(item.get("outline") or [], ensure_ascii=False, indent=2),
                    "",
                    str(item.get("text_excerpt") or ""),
                ]
            ),
        )
        return workspace

    def _write_workspace_collection(
        self,
        directory: Path,
        items: list[dict[str, Any]],
        name_for: Callable[[dict[str, Any]], str],
        content_for: Callable[[dict[str, Any]], str],
    ) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        for index, item in enumerate(items, start=1):
            filename = f"{index:03d}_{self._safe_workspace_filename(name_for(item))}.txt"
            self._write_workspace_file(directory / filename, content_for(item))

    def _write_workspace_file(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", errors="replace")

    def _safe_workspace_filename(self, value: str) -> str:
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-")
        return (name or "item")[:80]

    def _project_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    async def _call_openai_compatible(self, payload: dict[str, Any]) -> dict[str, Any]:
        prompt = (
            "You analyze Canvas course materials. Return strict JSON with keys: "
            "summary, timeline, course_outline, risks, confidence_notes. "
            "timeline items must include title, date, source, confidence, and confidence_reason. "
            "course_outline items must include title and evidence. "
            "Use only the provided synced course data and local tools. "
            "Use bash and grep only through the provided tools and only inside the project sandbox. "
            "Use notification tools only for high-confidence urgent items or explicit reminder instructions. "
            "Do not request Canvas, browser, MCP, or network access."
        )
        tool_root = payload.get("agent_project_root") or payload.get("agent_workspace_path")
        tools = build_course_agent_tools(payload, tool_root) + self.agent_tools
        agent = OpenAICompatAgent(
            AgentConfig(
                base_url=self.config.base_url or "",
                api_key=self.config.api_key or "",
                model=self.config.model,
                reasoning_effort=self.config.reasoning_effort,
            ),
            skills=SkillRegistry.from_text(self.config.skills),
        )
        run = await agent.run(
            system_prompt=prompt,
            user_payload=build_course_agent_input(payload, tool_root),
            tools=tools,
            response_format_json=True,
        )
        raw = run.content
        try:
            parsed = parse_model_json(raw)
            if not isinstance(parsed, dict):
                return {
                    "summary": raw,
                    "timeline": self._fallback_timeline(payload),
                    "course_outline": self._fallback_outline(payload),
                    "risks": ["Model returned JSON that was not an object."],
                    "confidence_notes": ["Fallback parsing was used."],
                }
            if run.tool_events:
                notes = parsed.setdefault("confidence_notes", [])
                if isinstance(notes, list):
                    notes.append(f"Agent used local tools: {', '.join(run.tools_used) or 'none'}.")
            return parsed
        except json.JSONDecodeError:
            return {
                "summary": raw,
                "timeline": self._fallback_timeline(payload),
                "course_outline": self._fallback_outline(payload),
                "risks": ["Model returned non-JSON content."],
                "confidence_notes": ["Fallback parsing was used."],
            }

    def _fallback_analysis(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "summary": f"{payload['course']['name']} has {len(payload['assignments'])} synced assignments, "
            f"{len(payload['announcements'])} announcements, and {len(payload['files'])} material files.",
            "timeline": self._fallback_timeline(payload),
            "course_outline": self._fallback_outline(payload),
            "risks": [],
            "confidence_notes": [
                "Generated without an external model because OPENAI_COMPAT_BASE_URL/API_KEY is not configured.",
                "Assignments are high-confidence Canvas structured data; announcement-derived dates are lower confidence.",
            ],
        }

    def _fallback_timeline(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for assignment in payload["assignments"]:
            date = assignment.get("due_at") or assignment.get("lock_at") or assignment.get("unlock_at")
            if not date:
                continue
            items.append(
                {
                    "title": assignment["name"],
                    "date": date,
                    "source": "assignment",
                    "confidence": "high",
                    "confidence_reason": "The date comes directly from Canvas structured assignment fields.",
                }
            )
        for announcement in payload["announcements"][:20]:
            title = announcement["title"]
            lowered = title.lower()
            if any(word in lowered for word in ["exam", "quiz", "deadline", "submission", "reminder"]):
                items.append(
                    {
                        "title": title,
                        "date": announcement.get("posted_at") or "",
                        "source": "announcement",
                        "confidence": "medium",
                        "confidence_reason": "The item is announcement-derived, so the date may be the announcement post time or inferred from announcement text.",
                    }
                )
        return sorted(items, key=lambda item: item.get("date") or "")

    def _fallback_outline(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for file_item in payload["files"]:
            for outline in file_item.get("outline") or []:
                title = outline.get("title")
                if title:
                    items.append({"title": title, "evidence": file_item["display_name"]})
                if len(items) >= 30:
                    return items
        return [{"title": file_item["display_name"], "evidence": "file name"} for file_item in payload["files"][:30]]

    def _save_analysis(self, course_id: int, kind: str, content: dict[str, Any], model: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO analyses(course_id, kind, content_json, model, generated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(course_id, kind) DO UPDATE SET
                    content_json=excluded.content_json,
                    model=excluded.model,
                    generated_at=excluded.generated_at
                """,
                (course_id, kind, json.dumps(content, ensure_ascii=False), model, utc_now()),
            )
