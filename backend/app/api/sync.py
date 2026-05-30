from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks
from fastapi.concurrency import run_in_threadpool

from ..runtime import launch_metadata_sync, state

router = APIRouter()


@router.post("/api/sync/run")
async def sync_run(background_tasks: BackgroundTasks) -> dict[str, Any]:
    return launch_metadata_sync(background_tasks)


@router.post("/api/courses/{course_id}/sync")
async def sync_course(course_id: int, background_tasks: BackgroundTasks) -> dict[str, Any]:
    return launch_metadata_sync(background_tasks, course_id=course_id)


@router.post("/api/sync/cancel")
async def sync_cancel() -> dict[str, Any]:
    if not state().sync_lock.locked():
        return {"status": "idle", "run": state().db.latest_sync_run()}
    state().sync_cancel_event.set()
    latest = state().db.latest_sync_run()
    if latest and latest.get("status") == "running":
        try:
            counts = json.loads(latest.get("counts_json") or "{}")
        except json.JSONDecodeError:
            counts = {}
        progress = counts.get("progress") if isinstance(counts.get("progress"), dict) else {}
        counts["progress"] = {
            **progress,
            "stage": "Interrupt requested",
            "status": "cancelling",
        }
        state().db.update_sync_run_counts(latest["id"], counts, message="Interrupt requested")
    return {"status": "cancelling", "run": state().db.latest_sync_run()}


@router.get("/api/sync/status")
async def sync_status() -> dict[str, Any]:
    def query() -> dict[str, Any]:
        return {
            "run": state().db.latest_sync_run(),
            "running": state().sync_lock.locked(),
            "cancel_requested": state().sync_cancel_event.is_set(),
        }

    return await run_in_threadpool(query)
