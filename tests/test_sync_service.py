from __future__ import annotations

from backend.app.sync_service import SyncService


def test_file_metadata_redacts_verifier_urls() -> None:
    service = SyncService.__new__(SyncService)
    redacted = service._redact_file_metadata(
        {
            "id": 1,
            "display_name": "slides.pdf",
            "url": "https://cityu-dg.instructure.com/files/1/download?verifier=secret",
            "thumbnail_url": "https://cityu-dg.instructure.com/thumbnail?verifier=secret",
            "preview_url": "https://cityu-dg.instructure.com/preview?verifier=secret",
        }
    )

    assert redacted["url"] is None
    assert redacted["thumbnail_url"] is None
    assert redacted["preview_url"] is None
