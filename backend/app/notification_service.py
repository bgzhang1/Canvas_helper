from __future__ import annotations

import re
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx

from agent import AgentTool
from .db import Database, utc_now


@dataclass(frozen=True)
class NotificationConfig:
    telegram_enabled: bool = False
    telegram_bot_token: str | None = None
    telegram_chat_id: str = ""
    email_enabled: bool = False
    email_target: str = ""
    email_from: str = "canvas-material@localhost"
    email_outbox_dir: Path = Path("./data/email_outbox")
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True


class NotificationService:
    def __init__(self, db: Database | None, config: NotificationConfig, *, telegram_client: httpx.Client | None = None):
        self.db = db
        self.config = config
        self._telegram_client = telegram_client

    def telegram_available(self) -> bool:
        return bool(
            self.config.telegram_enabled
            and self.config.telegram_bot_token
            and self.config.telegram_chat_id.strip()
        )

    def email_available(self) -> bool:
        return bool(self.config.email_enabled and self.config.email_target.strip())

    def send_telegram_message(self, text: str, *, disable_notification: bool = False) -> dict[str, Any]:
        if not self.telegram_available():
            return {"status": "disabled", "message": "Telegram notifications are not fully configured."}
        message = _clean_message(text, limit=3900)
        if not message:
            raise ValueError("Telegram message text is required")

        url = f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": self.config.telegram_chat_id,
            "text": message,
            "disable_notification": disable_notification,
        }
        client = self._telegram_client
        close_client = False
        if client is None:
            client = httpx.Client(timeout=15.0)
            close_client = True
        try:
            response = client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
        finally:
            if close_client:
                client.close()

        ok = bool(data.get("ok", True))
        status = "sent" if ok else "failed"
        self._add_event(
            action="telegram_sent" if ok else "telegram_failed",
            status="success" if ok else "failed",
            title="Telegram notification sent" if ok else "Telegram notification failed",
            message=None if ok else str(data),
            metadata={"chat_id": self._redacted_chat_id(), "message_length": len(message)},
        )
        return {"status": status, "ok": ok, "chat_id": self._redacted_chat_id()}

    def send_email_reminder(
        self,
        *,
        subject: str,
        body: str,
        due_at: str | None = None,
        priority: str = "normal",
    ) -> dict[str, Any]:
        if not self.email_available():
            return {"status": "disabled", "message": "Email reminders are not enabled or target is missing."}
        clean_subject = _clean_subject(subject)
        clean_body = _clean_message(body, limit=10000)
        if not clean_subject:
            raise ValueError("Email subject is required")
        if not clean_body:
            raise ValueError("Email body is required")

        message = EmailMessage()
        message["From"] = self.config.email_from
        message["To"] = self.config.email_target
        message["Subject"] = clean_subject
        if priority.lower() in {"high", "urgent"}:
            message["X-Priority"] = "1"
        if due_at:
            message["X-Canvas-Material-Due-At"] = due_at
        message.set_content(clean_body)

        if self.config.smtp_host:
            self._send_smtp(message)
            self._add_event(
                action="email_reminder_sent",
                status="success",
                title="Email reminder sent",
                metadata={"target": self._redacted_email(), "subject": clean_subject, "due_at": due_at},
            )
            return {"status": "sent", "target": self._redacted_email()}

        outbox_path = self._write_outbox(message)
        self._add_event(
            action="email_reminder_queued",
            status="warning",
            title="Email reminder queued",
            message="SMTP is not configured; reminder was written to the local outbox.",
            metadata={"target": self._redacted_email(), "subject": clean_subject, "due_at": due_at, "path": str(outbox_path)},
        )
        return {"status": "queued", "target": self._redacted_email(), "path": str(outbox_path)}

    def _send_smtp(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self.config.smtp_host or "", self.config.smtp_port, timeout=20) as smtp:
            if self.config.smtp_starttls:
                smtp.starttls()
            if self.config.smtp_username:
                smtp.login(self.config.smtp_username, self.config.smtp_password or "")
            smtp.send_message(message)

    def _write_outbox(self, message: EmailMessage) -> Path:
        self.config.email_outbox_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{utc_now().replace(':', '-').replace('+', '_')}_{_slug_filename(message['Subject'] or 'reminder')}.eml"
        path = self.config.email_outbox_dir / filename
        path.write_text(message.as_string(), encoding="utf-8")
        return path

    def _add_event(
        self,
        *,
        action: str,
        status: str,
        title: str,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self.db:
            return
        self.db.add_event(
            category="notification",
            action=action,
            status=status,
            title=title,
            message=message,
            metadata=metadata or {},
        )

    def _redacted_chat_id(self) -> str:
        value = self.config.telegram_chat_id.strip()
        if len(value) <= 4:
            return "****" if value else ""
        return value[:2] + "***" + value[-2:]

    def _redacted_email(self) -> str:
        value = self.config.email_target.strip()
        local, sep, domain = value.partition("@")
        if not sep:
            return "***"
        return (local[:2] + "***@" + domain) if len(local) > 2 else "***@" + domain


def build_notification_agent_tools(service: NotificationService) -> list[AgentTool]:
    tools: list[AgentTool] = []
    if service.telegram_available():
        tools.append(
            AgentTool(
                name="telegram_bot",
                description="Send a concise notification through the Telegram bot configured in Settings.",
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Message text to send."},
                        "disable_notification": {"type": "boolean", "default": False},
                    },
                    "required": ["text"],
                },
                handler=lambda args: service.send_telegram_message(
                    str(args.get("text") or ""),
                    disable_notification=bool(args.get("disable_notification", False)),
                ),
            )
        )
    if service.email_available():
        tools.append(
            AgentTool(
                name="email_reminder",
                description=(
                    "Send or queue an email reminder to the email target configured in Settings. "
                    "Use for high-confidence deadlines, urgent risks, or explicit reminder instructions."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                        "due_at": {"type": "string", "description": "Optional ISO date/time related to the reminder."},
                        "priority": {"type": "string", "enum": ["normal", "high", "urgent"], "default": "normal"},
                    },
                    "required": ["subject", "body"],
                },
                handler=lambda args: service.send_email_reminder(
                    subject=str(args.get("subject") or ""),
                    body=str(args.get("body") or ""),
                    due_at=str(args.get("due_at")) if args.get("due_at") else None,
                    priority=str(args.get("priority") or "normal"),
                ),
            )
        )
    return tools


def _clean_message(value: str, *, limit: int) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text[:limit]


def _clean_subject(value: str) -> str:
    return re.sub(r"[\r\n]+", " ", str(value or "")).strip()[:160]


def _slug_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-")[:80] or "reminder"
