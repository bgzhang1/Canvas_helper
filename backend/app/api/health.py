from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

from ..runtime import get_canvas_api_token, get_canvas_base_url, state

router = APIRouter()


@router.get("/api/health")
async def health() -> dict[str, Any]:
    def query() -> dict[str, Any]:
        return {
            "ok": True,
            "canvas_base_url": get_canvas_base_url(),
            "token_configured": bool(get_canvas_api_token()),
            "latest_sync": state().db.latest_sync_run(),
        }

    return await run_in_threadpool(query)
