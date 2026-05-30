from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

from ..runtime import state

router = APIRouter()


@router.get("/api/events")
async def event_logs(limit: int = 200, category: str | None = None) -> list[dict[str, Any]]:
    return await run_in_threadpool(state().db.list_events, limit, category=category)
