from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter

from ..canvas_client import CanvasReadOnlyClient, CanvasSecurityError
from ..runtime import (
    get_canvas_api_token,
    get_canvas_settings,
    get_notification_settings,
    get_sync_settings,
    restart_scheduler,
    state,
)
from ..schemas import (
    CanvasSettingsIn,
    CanvasSettingsTestIn,
    NotificationSettingsIn,
    SyncSettingsIn,
)

router = APIRouter()


@router.get("/api/settings")
async def app_settings() -> dict[str, Any]:
    canvas = get_canvas_settings()
    return {
        "canvas_base_url": canvas["canvas_base_url"],
        "token_configured": canvas["token_configured"],
        "sync": get_sync_settings(),
        "ocr": {
            "enabled": state().settings.ocr_enabled,
            "languages": state().settings.ocr_languages,
            "max_pages": state().settings.ocr_max_pages,
        },
        "notifications": get_notification_settings(),
    }


@router.put("/api/settings/canvas")
async def put_canvas_settings(payload: CanvasSettingsIn) -> dict[str, Any]:
    token = (payload.api_token or "").strip()
    if token:
        state().db.put_settings({"canvas.api_token": token})
    return get_canvas_settings()


@router.post("/api/settings/canvas/test")
async def test_canvas_settings(payload: CanvasSettingsTestIn) -> dict[str, Any]:
    token = (payload.api_token or "").strip() or get_canvas_api_token()
    if not token:
        return {
            "ok": False,
            "canvas_base_url": state().settings.canvas_base_url,
            "username": None,
            "message": "Canvas API token is not configured.",
        }
    async with CanvasReadOnlyClient(
        state().settings.canvas_base_url,
        token,
        timeout_seconds=state().settings.canvas_timeout_seconds,
        logger=logging.getLogger("canvas_audit"),
    ) as canvas:
        try:
            profile = await canvas.get_json("/api/v1/users/self/profile")
        except httpx.HTTPStatusError as exc:
            return {
                "ok": False,
                "canvas_base_url": state().settings.canvas_base_url,
                "username": None,
                "message": f"Canvas returned HTTP {exc.response.status_code}.",
            }
        except CanvasSecurityError as exc:
            return {
                "ok": False,
                "canvas_base_url": state().settings.canvas_base_url,
                "username": None,
                "message": str(exc),
            }
    username = profile.get("name") or profile.get("short_name") or profile.get("sortable_name") or profile.get("login_id")
    return {
        "ok": True,
        "canvas_base_url": state().settings.canvas_base_url,
        "username": username or "Canvas user",
        "message": "Canvas base URL and API token are usable.",
    }


@router.get("/api/settings/sync")
async def sync_settings() -> dict[str, Any]:
    return get_sync_settings()


@router.put("/api/settings/sync")
async def put_sync_settings(payload: SyncSettingsIn) -> dict[str, Any]:
    state().db.put_settings(
        {
            "sync.enabled": "true" if payload.enabled else "false",
            "sync.interval_minutes": str(payload.interval_minutes),
        }
    )
    restart_scheduler()
    return get_sync_settings()


@router.put("/api/settings/notifications")
async def put_notification_settings(payload: NotificationSettingsIn) -> dict[str, Any]:
    values = {
        "notify.telegram_enabled": "true" if payload.telegram_enabled else "false",
        "notify.telegram_chat_id": payload.telegram_chat_id,
        "notify.email_enabled": "true" if payload.email_enabled else "false",
        "notify.email_target": payload.email_target,
    }
    if payload.telegram_bot_token:
        values["notify.telegram_bot_token"] = payload.telegram_bot_token
    state().db.put_settings(values)
    return get_notification_settings()
