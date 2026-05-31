from __future__ import annotations

import json

from backend.app.db import Database, utc_now
from agent import AIAnalysisService, AIConfig


def test_fts_migration_backfills_materials_and_preserves_settings(tmp_path) -> None:
    db_path = tmp_path / "legacy.db"
    extracted = tmp_path / "extracted" / "course_1" / "601.txt"
    extracted.parent.mkdir(parents=True)
    extracted.write_text("Vector retrieval notes mention cosine ranking.", encoding="utf-8")

    legacy_db = Database(db_path)
    now = utc_now()
    with legacy_db.connect() as conn:
        legacy_db._create_initial_schema(conn)
        legacy_db._create_performance_indexes(conn)
        conn.execute("PRAGMA user_version = 2")
        conn.execute(
            """
            INSERT INTO settings(key, value, updated_at)
            VALUES ('sync.enabled', 'true', ?)
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO courses(id, name, course_code, workflow_state, term_name, raw_json, synced_at)
            VALUES (1, 'Search Systems', 'CS-FTS', 'available', '2026 Spring', '{}', ?)
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO announcements(id, course_id, title, message, posted_at, author_name, raw_json, synced_at)
            VALUES (101, 1, 'Exam notice', 'Final exam covers indexes.', ?, 'Teacher', '{}', ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO assignments(id, course_id, name, due_at, raw_json, synced_at)
            VALUES (201, 1, 'Capstone report', ?, ?, ?)
            """,
            (now, json.dumps({"description": "Submit the rubric analysis."}), now),
        )
        conn.execute(
            """
            INSERT INTO pages(course_id, page_url, title, body, raw_json, synced_at)
            VALUES (1, 'home', 'Home', 'Welcome to full text search.', '{}', ?)
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO files(
                id, course_id, display_name, filename, updated_at, extraction_status,
                extracted_text_path, outline_json, raw_json, synced_at
            )
            VALUES (601, 1, 'retrieval.txt', 'retrieval.txt', ?, 'extracted', ?, ?, '{}', ?)
            """,
            (now, str(extracted), json.dumps([{"title": "Ranking"}]), now),
        )

    db = Database(db_path)
    db.init()

    assert db.get_setting("sync.enabled") == "true"
    assert db.search_course_materials("exam indexes", limit=5)[0]["source"] == "announcement"
    assert db.search_course_materials("rubric analysis", limit=5)[0]["source"] == "assignment"
    file_match = db.search_course_materials("cosine ranking", course="CS-FTS", limit=5)[0]
    assert file_match["source"] == "file"
    assert file_match["title"] == "retrieval.txt"


def test_search_document_upsert_refreshes_fts_row(tmp_path) -> None:
    db = Database(tmp_path / "test.db")
    db.init()
    now = utc_now()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO courses(id, name, course_code, workflow_state, term_name, raw_json, synced_at)
            VALUES (1, 'Search Systems', 'CS-FTS', 'available', '2026 Spring', '{}', ?)
            """,
            (now,),
        )
        db.upsert_search_document(
            conn,
            source="page",
            source_id="1:home",
            course_id=1,
            title="Home",
            body="Initial sparse index notes.",
            updated_at=now,
        )

    assert db.search_course_materials("sparse index", limit=5)[0]["title"] == "Home"

    with db.connect() as conn:
        db.upsert_search_document(
            conn,
            source="page",
            source_id="1:home",
            course_id=1,
            title="Home",
            body="Updated dense retrieval notes.",
            updated_at=now,
        )

    assert db.search_course_materials("dense retrieval", limit=5)[0]["title"] == "Home"
    assert db.search_course_materials("sparse index", limit=5) == []


def test_fts_matches_spaced_query_against_compact_canvas_title(tmp_path) -> None:
    db = Database(tmp_path / "test.db")
    db.init()
    now = utc_now()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO courses(id, name, course_code, workflow_state, term_name, raw_json, synced_at)
            VALUES (1, 'CS2312 Problem Solving and Programming', 'Problem Solve & Programming', 'available', '2026 Spring', '{}', ?)
            """,
            (now,),
        )
        db.upsert_search_document(
            conn,
            source="assignment",
            source_id="week10",
            course_id=1,
            title="Week10 Lecture Popup Quiz",
            body="due_at: 2026-04-16T01:00:00Z",
            updated_at=now,
        )

    results = db.search_course_materials(
        "Week 10 Lecture Popup Quiz due before 09:00 lecture room",
        course="CS2312",
        limit=5,
    )

    assert results
    assert results[0]["title"] == "Week10 Lecture Popup Quiz"


def test_ai_analysis_search_tool_uses_fts_for_real_course_payload(tmp_path) -> None:
    db = Database(tmp_path / "test.db")
    db.init()
    now = utc_now()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO courses(id, name, course_code, workflow_state, term_name, raw_json, synced_at)
            VALUES (1, 'CS2312 Problem Solving and Programming', 'Problem Solve & Programming', 'available', '2026 Spring', '{}', ?)
            """,
            (now,),
        )
        db.upsert_search_document(
            conn,
            source="assignment",
            source_id="week11",
            course_id=1,
            title="Week11 Lecture Popup Quiz",
            body="due_at: 2026-04-23T01:00:00Z",
            updated_at=now,
        )

    service = AIAnalysisService(db, AIConfig(base_url=None, api_key=None, model="local"))
    payload = {
        "course": {"id": 1, "name": "CS2312 Problem Solving and Programming", "course_code": "Problem Solve & Programming"},
        "announcements": [],
        "assignments": [],
        "pages": [],
        "files": [],
    }

    results = service._search_course_materials(
        payload,
        {"query": "Week 11 Lecture Popup Quiz due before 09:00 lecture room", "sources": ["assignments"], "limit": 5},
    )

    assert results
    assert results[0]["title"] == "Week11 Lecture Popup Quiz"
    assert results[0]["source"] == "assignments"


def test_search_returns_none_when_fts_table_is_absent(tmp_path) -> None:
    db = Database(tmp_path / "test.db")
    db.init()
    with db.connect() as conn:
        conn.execute("DROP TABLE course_material_docs_fts")

    assert db.search_course_materials("anything") is None
