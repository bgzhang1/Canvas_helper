from __future__ import annotations

import json
import zipfile
from io import BytesIO

import fitz
import pytest
from fastapi.testclient import TestClient

from backend.app import main as app_module
from backend.app.config import Settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path,
        sqlite_path=tmp_path / "canvas_material_test.db",
        canvas_api_token=None,
    )
    monkeypatch.setattr(app_module, "get_settings", lambda: settings)

    with TestClient(app_module.app) as test_client:
        seed_database(tmp_path)
        yield test_client


def seed_database(data_dir) -> None:
    db = app_module.state().db
    now = "2026-05-28T00:00:00+00:00"
    local_file = data_dir / "canvas" / "course_1" / "Week 1" / "slides.txt"
    local_file.parent.mkdir(parents=True, exist_ok=True)
    local_file.write_text("Intro slides\nDeadline: next week\n", encoding="utf-8")
    extracted_file = data_dir / "extracted" / "course_1" / "slides.txt"
    extracted_file.parent.mkdir(parents=True, exist_ok=True)
    extracted_file.write_text("Extracted intro slides", encoding="utf-8")

    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO courses(id, name, course_code, workflow_state, term_name, raw_json, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (1, "Software Engineering", "CS101", "available", "2026 Spring", "{}", now),
        )
        conn.execute(
            """
            INSERT INTO announcements(id, course_id, title, message, posted_at, author_name, raw_json, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (101, 1, "Exam reminder", "<p>Read chapter 1</p>", now, "Teacher", "{}", now),
        )
        conn.execute(
            """
            INSERT INTO assignments(id, course_id, name, due_at, workflow_state, points_possible, raw_json, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                201,
                1,
                "Lab 1",
                now,
                "published",
                10,
                json.dumps(
                    {
                        "submission": {
                            "score": 8.5,
                            "grade": "8.5",
                            "submitted_at": now,
                            "workflow_state": "graded",
                        },
                        "submission_types": ["online_upload"],
                    }
                ),
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO calendar_events(id, course_id, title, start_at, event_type, raw_json, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (301, 1, "Lecture", now, "event", "{}", now),
        )
        conn.execute(
            """
            INSERT INTO pages(course_id, page_url, page_id, title, body, updated_at, published, raw_json, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, "front-page", 401, "Home", "<h1>Welcome</h1>", now, 1, json.dumps({"front_page": True}), now),
        )
        conn.execute(
            """
            INSERT INTO course_people(course_id, user_id, name, sortable_name, email, role, last_activity_at, raw_json, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, 501, "Ada Lovelace", "Lovelace, Ada", "ada@example.edu", "TeacherEnrollment", now, "{}", now),
        )
        conn.execute(
            """
            INSERT INTO files(
                id, course_id, display_name, filename, content_type, size, updated_at,
                local_path, backup_status, extraction_status, extracted_text_path,
                outline_json, raw_json, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                601,
                1,
                "slides.txt",
                "slides.txt",
                "text/plain",
                local_file.stat().st_size,
                now,
                str(local_file),
                "downloaded",
                "extracted",
                str(extracted_file),
                json.dumps([{"title": "Intro"}]),
                "{}",
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO files(
                id, course_id, display_name, filename, content_type, size, updated_at,
                local_path, backup_status, extraction_status, raw_json, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                602,
                1,
                "remote.pdf",
                "remote.pdf",
                "application/pdf",
                100,
                now,
                None,
                "pending",
                "pending",
                json.dumps({"canvas_folder_path": "Week 1"}),
                now,
            ),
        )
    db.add_event(category="sync", action="course_synced", status="success", title="Seeded course", course_id=1)


def test_read_endpoints_return_seeded_course_material(client: TestClient) -> None:
    assert client.get("/api/health").json()["ok"] is True

    courses = client.get("/api/courses").json()
    assert courses[0]["course_code"] == "CS101"
    assert courses[0]["file_count"] == 2
    assert courses[0]["downloaded_count"] == 1

    assert client.get("/api/courses/1/announcements").json()[0]["title"] == "Exam reminder"
    assignment = client.get("/api/courses/1/assignments").json()[0]
    assert assignment["name"] == "Lab 1"
    assert assignment["score"] == 8.5
    assert assignment["points_possible"] == 10
    assert assignment["submission_workflow_state"] == "graded"
    assert client.get("/api/courses/1/people").json()[0]["name"] == "Ada Lovelace"
    assert client.get("/api/courses/1/home").json()["title"] == "Home"
    files = client.get("/api/courses/1/files").json()
    assert next(item for item in files if item["id"] == 601)["outline"] == [{"title": "Intro"}]
    assert next(item for item in files if item["id"] == 601)["folder_path"] == "/Week 1"
    assert next(item for item in files if item["id"] == 602)["folder_path"] == "/Week 1"

    timeline = client.get("/api/courses/1/timeline").json()
    assert timeline["data_sources"]["assignments"]["count"] == 1
    assert "analysis" not in timeline
    detail = client.get("/api/courses/1/detail").json()
    assert detail["announcements"][0]["title"] == "Exam reminder"
    assert detail["assignments"][0]["name"] == "Lab 1"
    assert detail["assignments"][0]["score"] == 8.5
    assert detail["assignments"][0]["points_possible"] == 10
    assert next(item for item in detail["files"] if item["id"] == 601)["outline"] == [{"title": "Intro"}]
    assert detail["people"][0]["name"] == "Ada Lovelace"
    assert "analysis" not in detail["timeline"]
    assert detail["home"]["title"] == "Home"
    assert client.get("/api/courses/1/analysis").status_code == 404
    assert client.get("/api/events?limit=5").json()[0]["metadata"] == {}


def test_file_download_preview_and_zip_endpoints(client: TestClient) -> None:
    download = client.get("/api/courses/1/files/601/download")
    assert download.status_code == 200
    assert download.text.splitlines() == ["Intro slides", "Deadline: next week"]

    preview = client.get("/api/courses/1/files/601/preview")
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("application/pdf")
    preview_doc = fitz.open(stream=preview.content, filetype="pdf")
    try:
        assert "Intro slides" in preview_doc[0].get_text()
    finally:
        preview_doc.close()

    archive = client.post("/api/courses/1/files/download", json={"file_ids": [601, 602]})
    assert archive.status_code == 200
    with zipfile.ZipFile(BytesIO(archive.content)) as bundle:
        assert bundle.namelist() == ["slides.txt"]


def test_file_endpoint_guards_for_empty_missing_and_uncached_selection(client: TestClient) -> None:
    assert client.post("/api/courses/1/files/download", json={"file_ids": []}).status_code == 400
    assert client.post("/api/courses/1/files/backup", json={"file_ids": []}).status_code == 400
    assert client.get("/api/courses/1/files/999/download").status_code == 404
    assert client.get("/api/courses/1/files/602/download").status_code == 409
    assert client.post("/api/courses/1/files/602/extract").status_code == 409
    assert client.post("/api/courses/999/files/sync").status_code == 404
    assert client.post("/api/courses/999/analyze").status_code in {404, 405}


def test_settings_and_status_endpoints(client: TestClient) -> None:
    settings = client.get("/api/settings").json()
    assert settings["sync"]["enabled"] is False
    assert settings["token_configured"] is False
    assert "ai" not in settings

    assert client.post("/api/settings/canvas/test", json={"api_token": ""}).json()["ok"] is False
    assert client.put("/api/settings/canvas", json={"api_token": "test-token"}).json()["token_configured"] is True
    assert client.get("/api/settings/sync").json()["interval_minutes"] == 60
    assert client.put("/api/settings/sync", json={"enabled": False, "interval_minutes": 1}).status_code == 422
    assert client.put("/api/settings/sync", json={"enabled": False, "interval_minutes": 15}).json()["interval_minutes"] == 15
    assert client.put("/api/settings/ai", json={}).status_code in {404, 405}
    assert client.put(
        "/api/settings/notifications",
        json={
            "telegram_enabled": True,
            "telegram_bot_token": "bot-token",
            "telegram_chat_id": "123",
            "email_enabled": True,
            "email_target": "student@example.edu",
        },
    ).json()["telegram_configured"] is True

    assert client.get("/api/sync/status").json()["running"] is False
    assert client.post("/api/sync/cancel").json()["status"] == "idle"
    assert client.get("/api/analysis/status").status_code == 404
    assert client.post("/api/agent/chat", json={"message": "status"}).status_code in {404, 405}
    assert client.post("/api/agent/chat/stream", json={"message": "status"}).status_code in {404, 405}


def test_background_sync_routes_complete_without_external_services(client: TestClient) -> None:
    assert client.post("/api/sync/run").json()["status"] == "started"
    assert client.get("/api/sync/status").json()["run"]["status"] == "failed"

    assert client.post("/api/courses/1/sync").json()["status"] == "started"
    assert client.get("/api/sync/status").json()["run"]["status"] == "failed"
    assert client.post("/api/courses/1/analyze").status_code in {404, 405}
