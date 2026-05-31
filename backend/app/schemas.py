"""Pydantic request models shared across the API routers."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SyncSettingsIn(BaseModel):
    enabled: bool = False
    interval_minutes: int = Field(default=60, ge=15, le=24 * 60)


class CanvasSettingsIn(BaseModel):
    base_url: str | None = None
    api_token: str | None = None


class CanvasSettingsTestIn(BaseModel):
    base_url: str | None = None
    api_token: str | None = None


class AISettingsIn(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str = "gpt-4.1-mini"
    reasoning_effort: str = "medium"
    skills: str = ""


class AISettingsTestIn(BaseModel):
    base_url: str | None = None
    api_key: str | None = None


class AIModelIn(BaseModel):
    model: str = ""


class NotificationSettingsIn(BaseModel):
    telegram_enabled: bool = False
    telegram_bot_token: str | None = None
    telegram_chat_id: str = ""
    email_enabled: bool = False
    email_target: str = ""


class AgentChatMessageIn(BaseModel):
    role: str
    content: str


class AgentChatIn(BaseModel):
    message: str
    history: list[AgentChatMessageIn] = Field(default_factory=list)
    course_id: int | None = None
    session_id: str | None = None
    session_title: str | None = None


class FileSelectionIn(BaseModel):
    file_ids: list[int] = Field(default_factory=list)
