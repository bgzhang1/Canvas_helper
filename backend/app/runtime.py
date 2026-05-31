"""Application runtime state, service factories, and shared dependencies.

This module owns the process-wide ``AppState`` and the helpers that the API
routers depend on. Routers import from here; ``main`` imports routers plus this
module. Nothing here imports ``main``, which keeps the import graph acyclic.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, HTTPException

from agent import AIAnalysisService, AIConfig
from .backup_service import BackupService
from .canvas_client import CanvasReadOnlyClient
from .config import Settings
from .db import Database
from .extraction_service import ExtractionService
from .notification_service import NotificationConfig, NotificationService, build_notification_agent_tools
from .sync_service import SyncService


def initial_analysis_progress() -> dict[str, Any]:
    return {
        "running": False,
        "status": "idle",
        "percent": 0,
        "stage": "Idle",
        "course_id": None,
        "course": None,
        "file": None,
        "current": None,
        "total": None,
        "message": None,
    }


class AppState:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.db_path)
        self.sync_lock = asyncio.Lock()
        self.file_sync_lock = asyncio.Lock()
        self.analysis_lock = asyncio.Lock()
        self.sync_cancel_event = asyncio.Event()
        self.scheduler_task: asyncio.Task | None = None
        self.analysis_progress = initial_analysis_progress()


_app_state: AppState | None = None


def set_app_state(value: AppState | None) -> None:
    global _app_state
    _app_state = value


def state() -> AppState:
    if _app_state is None:
        raise RuntimeError("Application state has not been initialised")
    return _app_state


# FastAPI dependency providers ------------------------------------------------


def get_app_state() -> AppState:
    return state()


def get_db() -> Database:
    return state().db


# Logging ---------------------------------------------------------------------


def setup_audit_logger(settings: Settings) -> logging.Logger:
    logger = logging.getLogger("canvas_audit")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        settings.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(settings.audit_log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


# Service factories -----------------------------------------------------------


def make_canvas_client() -> CanvasReadOnlyClient:
    settings = state().settings
    return CanvasReadOnlyClient(
        get_canvas_base_url(),
        get_canvas_api_token(),
        timeout_seconds=settings.canvas_timeout_seconds,
        download_timeout_seconds=settings.canvas_download_timeout_seconds,
        logger=logging.getLogger("canvas_audit"),
    )


def make_extractor() -> ExtractionService:
    settings = state().settings
    return ExtractionService(
        state().db,
        settings.data_dir,
        ocr_enabled=settings.ocr_enabled,
        ocr_languages=settings.ocr_languages,
        ocr_max_pages=settings.ocr_max_pages,
    )


def make_ai_service() -> AIAnalysisService:
    ai_settings = get_ai_settings(include_secrets=True)
    notification_service = make_notification_service()
    return AIAnalysisService(
        state().db,
        AIConfig(
            base_url=ai_settings["base_url"] or None,
            api_key=ai_settings["api_key"] or None,
            model=ai_settings["model"],
            reasoning_effort=ai_settings["reasoning_effort"],
            skills=ai_settings["skills"],
        ),
        agent_tools=build_notification_agent_tools(notification_service),
    )


def make_notification_service() -> NotificationService:
    notification_settings = get_notification_settings(include_secrets=True)
    settings = state().settings
    return NotificationService(
        state().db,
        NotificationConfig(
            telegram_enabled=notification_settings["telegram_enabled"],
            telegram_bot_token=notification_settings.get("telegram_bot_token") or None,
            telegram_chat_id=notification_settings["telegram_chat_id"],
            email_enabled=notification_settings["email_enabled"],
            email_target=notification_settings["email_target"],
            email_from=settings.notification_email_from,
            email_outbox_dir=settings.data_dir / "email_outbox",
            smtp_host=settings.notification_smtp_host,
            smtp_port=settings.notification_smtp_port,
            smtp_username=settings.notification_smtp_username,
            smtp_password=settings.notification_smtp_password,
            smtp_starttls=settings.notification_smtp_starttls,
        ),
    )


# Shared request helpers ------------------------------------------------------


def course_label_or_404(course_id: int) -> str:
    with state().db.connect() as conn:
        row = conn.execute(
            "SELECT name, course_code FROM courses WHERE id = ?",
            (course_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Course {course_id} is not synced")
    return row["course_code"] or row["name"] or f"course_{course_id}"


def file_operation_status(backup_counts: dict[str, int], extraction_counts: dict[str, int]) -> str:
    failed = backup_counts.get("failed", 0) + extraction_counts.get("failed", 0)
    warnings = extraction_counts.get("partial", 0) + extraction_counts.get("skipped", 0)
    if failed:
        return "failed"
    if warnings:
        return "warning"
    return "success"


def get_canvas_api_token() -> str:
    settings = state().settings
    return state().db.get_setting("canvas.api_token", settings.canvas_api_token or "") or ""


def get_canvas_base_url() -> str:
    settings = state().settings
    return state().db.get_setting("canvas.base_url", settings.canvas_base_url) or settings.canvas_base_url


def get_canvas_settings() -> dict[str, Any]:
    return {
        "canvas_base_url": get_canvas_base_url(),
        "token_configured": bool(get_canvas_api_token()),
    }


def get_ai_settings(*, include_secrets: bool = False) -> dict[str, Any]:
    db = state().db
    settings = state().settings
    api_key = db.get_setting("ai.api_key", settings.openai_compat_api_key or "") or ""
    payload: dict[str, Any] = {
        "base_url": db.get_setting("ai.base_url", settings.openai_compat_base_url or "") or "",
        "configured": bool((db.get_setting("ai.base_url", settings.openai_compat_base_url or "") or "") and api_key),
        "api_key_configured": bool(api_key),
        "model": db.get_setting("ai.model", settings.openai_compat_model) or settings.openai_compat_model,
        "reasoning_effort": db.get_setting("ai.reasoning_effort", "medium") or "medium",
        "skills": db.get_setting("ai.skills", "") or "",
    }
    if include_secrets:
        payload["api_key"] = api_key
    return payload


def get_notification_settings(*, include_secrets: bool = False) -> dict[str, Any]:
    db = state().db
    telegram_token = db.get_setting("notify.telegram_bot_token", "") or ""
    payload: dict[str, Any] = {
        "telegram_enabled": db.get_setting("notify.telegram_enabled", "false") == "true",
        "telegram_configured": bool(telegram_token),
        "telegram_chat_id": db.get_setting("notify.telegram_chat_id", "") or "",
        "email_enabled": db.get_setting("notify.email_enabled", "false") == "true",
        "email_target": db.get_setting("notify.email_target", "") or "",
    }
    if include_secrets:
        payload["telegram_bot_token"] = telegram_token
    return payload


def get_sync_settings() -> dict[str, Any]:
    db = state().db
    return {
        "enabled": db.get_setting("sync.enabled", "false") == "true",
        "interval_minutes": int(db.get_setting("sync.interval_minutes", "60") or "60"),
    }


# Metadata sync orchestration -------------------------------------------------


async def _execute_metadata_sync(run_id: int, course_id: int | None) -> None:
    """Run a metadata sync under the global sync lock.

    ``course_id is None`` performs a full sync (all courses); otherwise it syncs
    a single course's non-file metadata. Shared by the background endpoints and
    the scheduler so the orchestration lives in exactly one place.
    """
    app_state = state()
    async with app_state.sync_lock:
        try:
            async with make_canvas_client() as canvas:
                backup = BackupService(app_state.db, canvas, app_state.settings.data_dir)
                service = SyncService(
                    app_state.db,
                    canvas,
                    backup,
                    make_extractor(),
                    is_cancelled=app_state.sync_cancel_event.is_set,
                )
                if course_id is None:
                    await service.sync_all(run_id, sync_files=False, download_files=False)
                else:
                    await service.sync_course_non_file(run_id, course_id)
        except Exception as exc:
            latest = app_state.db.latest_sync_run()
            if latest and latest.get("id") == run_id and latest.get("status") == "running":
                app_state.db.finish_sync_run(run_id, "failed", f"{exc.__class__.__name__}: {exc}")
            app_state.db.add_event(
                category="sync",
                action="sync_failed",
                status="failed",
                title="Course metadata sync failed" if course_id is not None else "Metadata sync failed",
                course_id=course_id,
                course_name=course_label_or_404(course_id) if course_id is not None else None,
                message=f"{exc.__class__.__name__}: {exc}",
            )
        finally:
            app_state.sync_cancel_event.clear()


def launch_metadata_sync(background_tasks: BackgroundTasks, *, course_id: int | None = None) -> dict[str, Any]:
    app_state = state()
    if app_state.sync_lock.locked():
        return {"status": "already_running", "run": app_state.db.latest_sync_run()}
    app_state.sync_cancel_event.clear()
    run_id = app_state.db.start_sync_run()
    background_tasks.add_task(_execute_metadata_sync, run_id, course_id)
    return {"status": "started", "run_id": run_id}


async def run_sync_job(*, course_id: int | None = None) -> dict[str, Any]:
    """Awaitable full sync used by the scheduler (returns when finished)."""
    app_state = state()
    if app_state.sync_lock.locked():
        return {"status": "already_running", "run": app_state.db.latest_sync_run()}
    app_state.sync_cancel_event.clear()
    run_id = app_state.db.start_sync_run()
    await _execute_metadata_sync(run_id, course_id)
    latest = app_state.db.latest_sync_run()
    status = latest.get("status") if latest and latest.get("id") == run_id else "succeeded"
    return {"status": status, "run_id": run_id}


async def sync_scheduler() -> None:
    while True:
        app_state = state()
        interval = int(app_state.db.get_setting("sync.interval_minutes", "60") or "60")
        enabled = app_state.db.get_setting("sync.enabled", "false") == "true"
        if enabled:
            try:
                await run_sync_job()
            except Exception:
                logging.getLogger(__name__).exception("scheduled sync failed")
        await asyncio.sleep(max(interval, 15) * 60)


def restart_scheduler() -> None:
    app_state = state()
    enabled = app_state.db.get_setting("sync.enabled", "false") == "true"
    if app_state.scheduler_task:
        app_state.scheduler_task.cancel()
        app_state.scheduler_task = None
    if enabled:
        app_state.scheduler_task = asyncio.create_task(sync_scheduler())
