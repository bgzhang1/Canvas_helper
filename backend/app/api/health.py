from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

from ..runtime import get_canvas_api_token, get_canvas_base_url, state

router = APIRouter()


@router.get("/api/health")
async def health() -> dict[str, Any]:
    def query() -> dict[str, Any]:
        db_ok = True
        latest_sync = None
        try:
            with state().db.connect() as conn:
                row = conn.execute("PRAGMA quick_check").fetchone()
                db_ok = bool(row) and str(row[0]).lower() == "ok"
            latest_sync = state().db.latest_sync_run()
        except Exception:
            db_ok = False
        return {
            "ok": db_ok,
            "db_ok": db_ok,
            "canvas_base_url": get_canvas_base_url(),
            "token_configured": bool(get_canvas_api_token()),
            "latest_sync": latest_sync,
        }

    return await run_in_threadpool(query)
