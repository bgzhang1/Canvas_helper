from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    canvas_base_url: str = "https://cityu-dg.instructure.com/"
    canvas_api_token: str | None = Field(default=None, repr=False)

    data_dir: Path = Path("./data")
    sqlite_path: Path | None = None

    ocr_enabled: bool = True
    ocr_languages: str = "eng+chi_sim"
    ocr_max_pages: int = 20

    canvas_timeout_seconds: float = 60.0
    canvas_download_timeout_seconds: float = 180.0

    notification_email_from: str = "canvas-material@localhost"
    notification_smtp_host: str | None = None
    notification_smtp_port: int = 587
    notification_smtp_username: str | None = None
    notification_smtp_password: str | None = Field(default=None, repr=False)
    notification_smtp_starttls: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("canvas_base_url")
    @classmethod
    def normalize_canvas_base_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https":
            raise ValueError("CANVAS_BASE_URL must use https")
        if not parsed.netloc:
            raise ValueError("CANVAS_BASE_URL must include a host")
        return value.rstrip("/") + "/"

    @property
    def db_path(self) -> Path:
        return self.sqlite_path or self.data_dir / "canvas_material.db"

    @property
    def audit_log_path(self) -> Path:
        return self.data_dir / "audit" / "canvas_readonly.log"


@lru_cache
def get_settings() -> Settings:
    return Settings()
