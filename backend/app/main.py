from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import runtime
from .api import agent, courses, events, files, health, settings as settings_api, sync
from .config import get_settings

# Re-exported so tests and tooling can reach runtime state via this module
# (e.g. monkeypatching ``main.get_settings`` or calling ``main.state()``).
from .runtime import state  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    runtime.setup_audit_logger(settings)
    app_state = runtime.AppState(settings)
    runtime.set_app_state(app_state)
    app.state.app_state = app_state
    app_state.db.init()
    app_state.db.mark_stale_sync_runs_interrupted()
    app_state.db.mark_stale_downloads_interrupted()
    if app_state.db.get_setting("sync.enabled", "false") == "true":
        app_state.scheduler_task = asyncio.create_task(runtime.sync_scheduler())
    yield
    if app_state.scheduler_task:
        app_state.scheduler_task.cancel()
        try:
            await app_state.scheduler_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Canvas Material Manager", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["*"],
)

for module in (health, courses, files, sync, settings_api, agent, events):
    app.include_router(module.router)


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
