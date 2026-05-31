from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException

from ..canvas_client import CanvasReadOnlyClient, CanvasSecurityError
from ..runtime import (
    get_ai_settings,
    get_canvas_api_token,
    get_canvas_base_url,
    get_canvas_settings,
    get_notification_settings,
    get_sync_settings,
    restart_scheduler,
    state,
)
from ..schemas import (
    AIModelIn,
    AISettingsIn,
    AISettingsTestIn,
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
        "ai": get_ai_settings(),
        "notifications": get_notification_settings(),
    }


def _normalize_canvas_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Canvas base URL must be a valid https URL")
    return value.rstrip("/") + "/"


@router.put("/api/settings/canvas")
async def put_canvas_settings(payload: CanvasSettingsIn) -> dict[str, Any]:
    updates: dict[str, str] = {}
    base_url = (payload.base_url or "").strip()
    if base_url:
        updates["canvas.base_url"] = _normalize_canvas_base_url(base_url)
    token = (payload.api_token or "").strip()
    if token:
        updates["canvas.api_token"] = token
    if updates:
        state().db.put_settings(updates)
    return get_canvas_settings()


@router.post("/api/settings/canvas/test")
async def test_canvas_settings(payload: CanvasSettingsTestIn) -> dict[str, Any]:
    base_url = (payload.base_url or "").strip() or get_canvas_base_url()
    token = (payload.api_token or "").strip() or get_canvas_api_token()
    if not token:
        return {
            "ok": False,
            "canvas_base_url": base_url,
            "username": None,
            "message": "Canvas API token is not configured.",
        }
    async with CanvasReadOnlyClient(
        base_url,
        token,
        timeout_seconds=state().settings.canvas_timeout_seconds,
        logger=logging.getLogger("canvas_audit"),
    ) as canvas:
        try:
            profile = await canvas.get_json("/api/v1/users/self/profile")
        except httpx.HTTPStatusError as exc:
            return {
                "ok": False,
                "canvas_base_url": base_url,
                "username": None,
                "message": f"Canvas returned HTTP {exc.response.status_code}.",
            }
        except CanvasSecurityError as exc:
            return {
                "ok": False,
                "canvas_base_url": base_url,
                "username": None,
                "message": str(exc),
            }
        except httpx.RequestError as exc:
            return {
                "ok": False,
                "canvas_base_url": base_url,
                "username": None,
                "message": f"Canvas connection failed: {exc.__class__.__name__}: {exc}",
            }
    username = profile.get("name") or profile.get("short_name") or profile.get("sortable_name") or profile.get("login_id")
    return {
        "ok": True,
        "canvas_base_url": base_url,
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


@router.put("/api/settings/ai")
async def put_ai_settings(payload: AISettingsIn) -> dict[str, Any]:
    values = {
        "ai.base_url": payload.base_url or "",
        "ai.model": payload.model.strip() or "gpt-4.1-mini",
        "ai.reasoning_effort": payload.reasoning_effort if payload.reasoning_effort in {"low", "medium", "high"} else "medium",
        "ai.skills": payload.skills,
    }
    if payload.api_key:
        values["ai.api_key"] = payload.api_key
    state().db.put_settings(values)
    return get_ai_settings()


def _ai_models_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    low = base.lower()
    if low.endswith("/chat/completions"):
        return base[: -len("/chat/completions")] + "/models"
    if low.endswith("/v1") or low.endswith("/openai/v1") or low.endswith("/v1/openai"):
        return f"{base}/models"
    return f"{base}/v1/models"


async def _fetch_ai_models(base_url: str | None, api_key: str | None) -> dict[str, Any]:
    saved = get_ai_settings(include_secrets=True)
    base = (base_url or saved["base_url"] or "").strip()
    key = (api_key or saved.get("api_key") or "").strip()
    if not base or not key:
        return {"ok": False, "models": [], "message": "AI engine is not configured. Set COMPAT_BASE_URL and API_KEY first."}
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(_ai_models_url(base), headers={"Authorization": f"Bearer {key}"})
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "models": [], "message": f"Provider returned HTTP {exc.response.status_code}."}
    except httpx.RequestError as exc:
        return {"ok": False, "models": [], "message": f"Connection failed: {exc.__class__.__name__}: {exc}"}
    items = data.get("data") if isinstance(data, dict) else data
    models = sorted({str(item["id"]) for item in items if isinstance(item, dict) and item.get("id")}) if isinstance(items, list) else []
    return {"ok": True, "models": models, "message": f"{len(models)} models available."}


@router.post("/api/settings/ai/test")
async def test_ai_settings(payload: AISettingsTestIn) -> dict[str, Any]:
    result = await _fetch_ai_models(payload.base_url, payload.api_key)
    return {"ok": result["ok"], "message": result["message"], "model_count": len(result["models"])}


@router.get("/api/settings/ai/models")
async def list_ai_models() -> dict[str, Any]:
    return {**await _fetch_ai_models(None, None), "model": get_ai_settings()["model"]}


@router.put("/api/settings/ai/model")
async def put_ai_model(payload: AIModelIn) -> dict[str, Any]:
    model = (payload.model or "").strip()
    if model:
        state().db.put_settings({"ai.model": model})
    return get_ai_settings()


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
