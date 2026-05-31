from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    canvas_base_url: str = "https://canvas.example.edu/"
    canvas_api_token: str | None = Field(default=None, repr=False)

    openai_compat_base_url: str | None = None
    openai_compat_api_key: str | None = Field(default=None, repr=False)
    openai_compat_model: str = "gpt-4.1-mini"

    data_dir: Path = Path("./data")
    sqlite_path: Path | None = None

    ocr_enabled: bool = True
    ocr_languages: str = "eng+chi_sim"
    ocr_max_pages: int = 20
    ocr_timeout_seconds: int = 30

    canvas_timeout_seconds: float = 60.0
    canvas_download_timeout_seconds: float = 180.0
    canvas_max_retries: int = 2
    canvas_max_pages: int = 200
    canvas_max_download_bytes: int = 2 * 1024 * 1024 * 1024  # 2 GiB per file
    backup_min_free_bytes: int = 512 * 1024 * 1024  # keep 512 MiB headroom

    # Bash/grep tools expose broad local filesystem access to the model and are
    # off by default; enable explicitly only on a trusted machine.
    agent_shell_tools_enabled: bool = False

    # Interactive chat agent runtime budget (tunable for slow/cheap providers).
    agent_request_timeout_seconds: float = 120.0
    agent_tool_timeout_seconds: float = 60.0
    agent_max_tool_rounds: int = 6

    notification_email_from: str = "canvas-helper@localhost"
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
        return self.sqlite_path or self.data_dir / "canvas_helper.db"

    @property
    def audit_log_path(self) -> Path:
        return self.data_dir / "audit" / "canvas_readonly.log"


@lru_cache
def get_settings() -> Settings:
    return Settings()
