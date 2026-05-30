from __future__ import annotations

import json

import httpx

from backend.app.db import Database
from backend.app.notification_service import (
    NotificationConfig,
    NotificationService,
    build_notification_agent_tools,
)


def test_telegram_agent_tool_uses_configured_bot(tmp_path) -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append({"url": str(request.url), "body": json.loads(request.content)})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    db = Database(tmp_path / "test.db")
    db.init()
    service = NotificationService(
        db,
        NotificationConfig(
            telegram_enabled=True,
            telegram_bot_token="123:abc",
            telegram_chat_id="987654",
            email_outbox_dir=tmp_path / "outbox",
        ),
        telegram_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    tool = next(item for item in build_notification_agent_tools(service) if item.name == "telegram_bot")

    result = tool.handler({"text": "Reminder: Lab 1 is due tomorrow."})

    assert result["status"] == "sent"
    assert requests[0]["url"] == "https://api.telegram.org/bot123:abc/sendMessage"
    assert requests[0]["body"]["chat_id"] == "987654"
    assert requests[0]["body"]["text"] == "Reminder: Lab 1 is due tomorrow."
    assert db.list_events(1)[0]["action"] == "telegram_sent"


def test_email_reminder_agent_tool_writes_outbox_without_smtp(tmp_path) -> None:
    db = Database(tmp_path / "test.db")
    db.init()
    service = NotificationService(
        db,
        NotificationConfig(
            email_enabled=True,
            email_target="student@example.edu",
            email_from="canvas@example.edu",
            email_outbox_dir=tmp_path / "outbox",
        ),
    )
    tool = next(item for item in build_notification_agent_tools(service) if item.name == "email_reminder")

    result = tool.handler(
        {
            "subject": "Lab 1 reminder",
            "body": "Lab 1 is due on 2026-06-04.",
            "due_at": "2026-06-04T00:00:00+00:00",
            "priority": "high",
        }
    )

    outbox_path = tmp_path / "outbox"
    files = list(outbox_path.glob("*.eml"))
    assert result["status"] == "queued"
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "To: student@example.edu" in content
    assert "Subject: Lab 1 reminder" in content
    assert "X-Canvas-Material-Due-At: 2026-06-04T00:00:00+00:00" in content
    assert db.list_events(1)[0]["action"] == "email_reminder_queued"


def test_notification_tools_only_include_enabled_channels(tmp_path) -> None:
    service = NotificationService(
        None,
        NotificationConfig(
            telegram_enabled=True,
            telegram_bot_token="123:abc",
            telegram_chat_id="42",
            email_enabled=False,
            email_target="student@example.edu",
            email_outbox_dir=tmp_path / "outbox",
        ),
    )

    assert [tool.name for tool in build_notification_agent_tools(service)] == ["telegram_bot"]
