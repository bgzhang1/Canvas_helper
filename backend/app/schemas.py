"""Pydantic request models shared across the API routers."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SyncSettingsIn(BaseModel):
    enabled: bool = False
    interval_minutes: int = Field(default=60, ge=15, le=24 * 60)


class CanvasSettingsIn(BaseModel):
    api_token: str | None = None


class CanvasSettingsTestIn(BaseModel):
    api_token: str | None = None


class NotificationSettingsIn(BaseModel):
    telegram_enabled: bool = False
    telegram_bot_token: str | None = None
    telegram_chat_id: str = ""
    email_enabled: bool = False
    email_target: str = ""


class FileSelectionIn(BaseModel):
    file_ids: list[int] = Field(default_factory=list)
