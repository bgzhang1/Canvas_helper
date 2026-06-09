from __future__ import annotations

import asyncio

from backend.app.backup_service import BackupService
from backend.app.db import Database, utc_now


class FakeCanvas:
    def __init__(self, *, file_metadata: dict | None = None, folders: dict[int, dict] | None = None) -> None:
        self.detail_requests: list[str] = []
        self.download_urls: list[str] = []
        self.destinations = []
        self.file_metadata = file_metadata or {
            "id": 42,
            "url": "https://cityu-dg.instructure.com/files/42/download?verifier=secret",
            "updated_at": "2026-05-24T00:00:00Z",
            "size": 4,
        }
        self.folders = folders or {}

    async def get_json(self, path: str) -> dict:
        self.detail_requests.append(path)
        if path.startswith("/api/v1/folders/"):
            return self.folders[int(path.rsplit("/", 1)[1])]
        return self.file_metadata

    async def download_to_file(self, url, destination):
        self.download_urls.append(url)
        self.destinations.append(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"test")
        return "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08", 4


class FailingDownloadCanvas(FakeCanvas):
    async def download_to_file(self, url, destination):
        raise RuntimeError("download denied")


def test_backup_fetches_download_url_at_runtime(tmp_path) -> None:
    db = Database(tmp_path / "test.db")
    db.init()
    now = utc_now()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO courses(id, name, raw_json, synced_at)
            VALUES (1, 'Course', '{}', ?)
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO files(id, course_id, display_name, filename, size, updated_at, canvas_url, raw_json, synced_at)
            VALUES (42, 1, 'file.txt', 'file.txt', 4, '2026-05-24T00:00:00Z', NULL, '{}', ?)
            """,
            (now,),
        )

    canvas = FakeCanvas()

    counts = asyncio.run(BackupService(db, canvas, tmp_path).backup_course_files(1))

    assert counts["downloaded"] == 1
    assert canvas.detail_requests == ["/api/v1/files/42"]
    assert canvas.download_urls == ["https://cityu-dg.instructure.com/files/42/download?verifier=secret"]
    with db.connect() as conn:
        row = conn.execute("SELECT canvas_url, backup_status FROM files WHERE id = 42").fetchone()
    assert row["canvas_url"] is None
    assert row["backup_status"] == "downloaded"


def test_backup_preserves_canvas_folder_path(tmp_path) -> None:
    db = Database(tmp_path / "test.db")
    db.init()
    now = utc_now()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO courses(id, name, raw_json, synced_at)
            VALUES (1, 'Course', '{}', ?)
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO files(id, course_id, display_name, filename, size, updated_at, canvas_url, raw_json, synced_at)
            VALUES (42, 1, 'slides.pdf', 'slides.pdf', 4, '2026-05-24T00:00:00Z', NULL, '{}', ?)
            """,
            (now,),
        )

    canvas = FakeCanvas(
        file_metadata={
            "id": 42,
            "display_name": "slides.pdf",
            "url": "https://cityu-dg.instructure.com/files/42/download?verifier=secret",
            "updated_at": "2026-05-24T00:00:00Z",
            "size": 4,
            "folder_id": 9,
        },
        folders={9: {"id": 9, "full_name": "course files/Lectures/Week 1"}},
    )

    counts = asyncio.run(BackupService(db, canvas, tmp_path).backup_course_files(1))

    expected = tmp_path / "canvas" / "course_1" / "Lectures" / "Week 1" / "slides.pdf"
    assert counts["downloaded"] == 1
    assert canvas.detail_requests == ["/api/v1/files/42", "/api/v1/folders/9"]
    assert canvas.destinations == [expected]
    assert expected.read_bytes() == b"test"
    with db.connect() as conn:
        row = conn.execute("SELECT local_path FROM files WHERE id = 42").fetchone()
    assert row["local_path"] == str(expected)


def test_backup_marks_failed_downloads(tmp_path) -> None:
    db = Database(tmp_path / "test.db")
    db.init()
    now = utc_now()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO courses(id, name, raw_json, synced_at)
            VALUES (1, 'Course', '{}', ?)
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO files(id, course_id, display_name, filename, size, updated_at, canvas_url, raw_json, synced_at)
            VALUES (42, 1, 'file.txt', 'file.txt', 4, '2026-05-24T00:00:00Z', NULL, '{}', ?)
            """,
            (now,),
        )

    counts = asyncio.run(BackupService(db, FailingDownloadCanvas(), tmp_path).backup_course_files(1))

    assert counts["failed"] == 1
    with db.connect() as conn:
        row = conn.execute("SELECT backup_status, backup_error FROM files WHERE id = 42").fetchone()
    assert row["backup_status"] == "fail_download"
    assert "download denied" in row["backup_error"]
