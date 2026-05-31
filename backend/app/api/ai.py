from __future__ import annotations

import json
import traceback
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse

from ..ai.chat import AGENT_SYSTEM_PROMPT, build_agent, build_agent_chat_context, build_agent_tools
from ..runtime import (
    course_label_or_404,
    get_ai_settings,
    initial_analysis_progress,
    make_ai_service,
    state,
)
from ..schemas import AgentChatIn

router = APIRouter()


def _describe_error(exc: Exception) -> tuple[str, dict[str, Any]]:
    meta: dict[str, Any] = {"error_type": exc.__class__.__name__, "traceback": traceback.format_exc()[-2000:]}
    if isinstance(exc, httpx.HTTPStatusError):
        meta["status_code"] = exc.response.status_code
        meta["request_url"] = str(exc.request.url)
        try:
            meta["response_body"] = exc.response.text[:1000]
        except Exception:
            meta["response_body"] = None
        return f"HTTP {exc.response.status_code} from {exc.request.url}: {meta.get('response_body') or exc}", meta
    if isinstance(exc, httpx.TimeoutException):
        meta["status_code"] = "timeout"
    return f"{exc.__class__.__name__}: {exc}", meta


@router.get("/api/analysis/status")
async def analysis_status() -> dict[str, Any]:
    return state().analysis_progress


@router.post("/api/agent/chat")
async def agent_chat(payload: AgentChatIn) -> dict[str, Any]:
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    session_id = (payload.session_id or "").strip()[:80] or None
    session_title = (payload.session_title or "").strip()[:120] or None
    event_metadata: dict[str, Any] = {
        "session_id": session_id,
        "session_title": session_title,
        "message_preview": message[:240],
        "history_count": len(payload.history),
    }
    state().db.add_event(
        category="ai",
        action="agent_chat_started",
        status="running",
        title="Agent chat started",
        course_id=payload.course_id,
        item_id=session_id,
        item_name=session_title,
        metadata=event_metadata,
    )

    ai_settings = get_ai_settings(include_secrets=True)
    if not ai_settings["base_url"] or not ai_settings.get("api_key"):
        state().db.add_event(
            category="ai",
            action="agent_chat_not_configured",
            status="warning",
            title="Agent chat skipped",
            course_id=payload.course_id,
            item_id=session_id,
            item_name=session_title,
            message="AI agent is not configured.",
            metadata=event_metadata,
        )
        return {
            "role": "assistant",
            "content": "AI agent is not configured. Set COMPAT_BASE_URL and API_KEY in Settings first.",
            "tools_used": [],
            "status": "not_configured",
        }

    context = build_agent_chat_context(payload.course_id)
    history = [
        {"role": item.role, "content": item.content[:6000]}
        for item in payload.history[-12:]
        if item.role in {"user", "assistant"} and item.content.strip()
    ]
    agent = build_agent(ai_settings)
    tools = build_agent_tools()
    try:
        run = await agent.run(
            system_prompt=AGENT_SYSTEM_PROMPT,
            user_payload={
                "message": message,
                "history": history,
                "context": context,
            },
            tools=tools,
            response_format_json=False,
        )
    except Exception as exc:
        error_message, error_meta = _describe_error(exc)
        state().db.add_event(
            category="ai",
            action="agent_chat_failed",
            status="failed",
            title="Agent chat failed",
            course_id=payload.course_id,
            item_id=session_id,
            item_name=session_title,
            message=error_message,
            metadata={**event_metadata, **error_meta},
        )
        raise
    content = run.content.strip() or "(empty response)"
    state().db.add_event(
        category="ai",
        action="agent_chat_completed",
        status="success",
        title="Agent chat completed",
        course_id=payload.course_id,
        item_id=session_id,
        item_name=session_title,
        metadata={
            **event_metadata,
            "tools_used": run.tools_used,
            "tool_events": [{"name": event.name, "ok": event.ok, "error": event.error} for event in run.tool_events],
            "fallback_without_tools": run.fallback_without_tools,
            "response_preview": content[:240],
        },
    )
    return {
        "role": "assistant",
        "content": content,
        "tools_used": run.tools_used,
        "status": "ok",
    }


@router.post("/api/agent/chat/stream")
async def agent_chat_stream(payload: AgentChatIn) -> StreamingResponse:
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
    return StreamingResponse(_agent_chat_stream_events(payload, message), media_type="application/x-ndjson; charset=utf-8")


async def _agent_chat_stream_events(payload: AgentChatIn, message: str):
    def event_line(event: dict[str, Any]) -> str:
        return json.dumps(event, ensure_ascii=False) + "\n"

    session_id = (payload.session_id or "").strip()[:80] or None
    session_title = (payload.session_title or "").strip()[:120] or None
    event_metadata: dict[str, Any] = {
        "session_id": session_id,
        "session_title": session_title,
        "message_preview": message[:240],
        "history_count": len(payload.history),
        "stream": True,
    }
    state().db.add_event(
        category="ai",
        action="agent_chat_started",
        status="running",
        title="Agent chat started",
        course_id=payload.course_id,
        item_id=session_id,
        item_name=session_title,
        metadata=event_metadata,
    )
    yield event_line({"type": "status", "status": "running"})

    ai_settings = get_ai_settings(include_secrets=True)
    if not ai_settings["base_url"] or not ai_settings.get("api_key"):
        content = "AI agent is not configured. Set COMPAT_BASE_URL and API_KEY in Settings first."
        state().db.add_event(
            category="ai",
            action="agent_chat_not_configured",
            status="warning",
            title="Agent chat skipped",
            course_id=payload.course_id,
            item_id=session_id,
            item_name=session_title,
            message="AI agent is not configured.",
            metadata=event_metadata,
        )
        yield event_line({"type": "delta", "content": content})
        yield event_line(
            {
                "type": "done",
                "message": {"role": "assistant", "content": content, "tools_used": [], "status": "not_configured"},
                "tools_used": [],
            }
        )
        return

    context = build_agent_chat_context(payload.course_id)
    history = [
        {"role": item.role, "content": item.content[:6000]}
        for item in payload.history[-12:]
        if item.role in {"user", "assistant"} and item.content.strip()
    ]
    agent = build_agent(ai_settings)
    tools = build_agent_tools()
    content_parts: list[str] = []
    latest_done: dict[str, Any] = {}
    try:
        async for event in agent.run_stream(
            system_prompt=AGENT_SYSTEM_PROMPT,
            user_payload={
                "message": message,
                "history": history,
                "context": context,
            },
            tools=tools,
            response_format_json=False,
        ):
            if event.get("type") == "delta":
                content_parts.append(str(event.get("content") or ""))
                yield event_line(event)
            elif event.get("type") == "tool":
                if event.get("phase") != "start":
                    state().db.add_event(
                        category="ai",
                        action="agent_tool_call",
                        status="success" if event.get("ok") else "failed",
                        title=f"Tool {event.get('name')}",
                        course_id=payload.course_id,
                        item_id=session_id,
                        item_name=str(event.get("name") or ""),
                        message=event.get("error"),
                        metadata={
                            "session_id": session_id,
                            "tool": event.get("name"),
                            "arguments": event.get("arguments"),
                            "ok": event.get("ok"),
                            "error": event.get("error"),
                        },
                    )
                yield event_line(event)
            elif event.get("type") == "done":
                latest_done = event
    except Exception as exc:
        error_message, error_meta = _describe_error(exc)
        state().db.add_event(
            category="ai",
            action="agent_chat_failed",
            status="failed",
            title="Agent chat failed",
            course_id=payload.course_id,
            item_id=session_id,
            item_name=session_title,
            message=error_message,
            metadata={**event_metadata, **error_meta},
        )
        yield event_line({"type": "error", "message": error_message, **error_meta})
        return

    content = "".join(content_parts).strip() or str(latest_done.get("content") or "").strip() or "(empty response)"
    tools_used = latest_done.get("tools_used") or []
    tool_events = latest_done.get("tool_events") or []
    state().db.add_event(
        category="ai",
        action="agent_chat_completed",
        status="success",
        title="Agent chat completed",
        course_id=payload.course_id,
        item_id=session_id,
        item_name=session_title,
        metadata={
            **event_metadata,
            "tools_used": tools_used,
            "tool_events": tool_events,
            "fallback_without_tools": bool(latest_done.get("fallback_without_tools")),
            "response_preview": content[:240],
        },
    )
    yield event_line(
        {
            "type": "done",
            "message": {
                "role": "assistant",
                "content": content,
                "tools_used": tools_used,
                "status": "ok",
            },
            "tools_used": tools_used,
        }
    )


@router.post("/api/courses/{course_id}/analyze")
async def analyze(course_id: int, background_tasks: BackgroundTasks) -> dict[str, Any]:
    if state().analysis_progress.get("running") or state().analysis_lock.locked():
        return {"status": "already_running", "progress": state().analysis_progress}

    course_label = course_label_or_404(course_id)
    state().analysis_progress = {
        **initial_analysis_progress(),
        "running": True,
        "status": "running",
        "percent": 2,
        "stage": "Preparing AI analysis",
        "course_id": course_id,
        "course": course_label,
    }
    state().db.add_event(
        category="ai",
        action="analysis_started",
        status="running",
        title="AI analysis started",
        course_id=course_id,
        course_name=course_label,
    )

    def report_progress(**progress: Any) -> None:
        current = {
            **state().analysis_progress,
            **progress,
            "course_id": course_id,
            "course": course_label,
            "message": None,
        }
        if "status" not in progress:
            current["status"] = "running"
        if "running" not in progress:
            current["running"] = True
        if current.get("stage") != "Reading extracted files":
            current["file"] = None
            current["current"] = None
            current["total"] = None
        percent = current.get("percent")
        current["percent"] = max(0, min(100, int(percent if isinstance(percent, (int, float)) else 0)))
        state().analysis_progress = current

    async def job() -> None:
        async with state().analysis_lock:
            try:
                analysis_result = await make_ai_service().analyze_course(course_id, on_progress=report_progress)
                report_progress(
                    percent=100,
                    stage="AI analysis completed",
                    status="succeeded",
                    running=False,
                )
                state().db.add_event(
                    category="ai",
                    action="analysis_completed",
                    status="success",
                    title="AI analysis completed",
                    course_id=course_id,
                    course_name=course_label,
                    metadata={"model": analysis_result.get("model")},
                )
            except Exception as exc:
                state().analysis_progress = {
                    **state().analysis_progress,
                    "running": False,
                    "status": "failed",
                    "stage": "AI analysis failed",
                    "message": f"{exc.__class__.__name__}: {exc}",
                }
                state().db.add_event(
                    category="ai",
                    action="analysis_failed",
                    status="failed",
                    title="AI analysis failed",
                    course_id=course_id,
                    course_name=course_label,
                    message=f"{exc.__class__.__name__}: {exc}",
                )

    background_tasks.add_task(job)
    return {"status": "started", "progress": state().analysis_progress}


@router.get("/api/courses/{course_id}/analysis")
async def get_analysis(course_id: int) -> dict[str, Any] | None:
    return state().db.get_analysis(course_id)
