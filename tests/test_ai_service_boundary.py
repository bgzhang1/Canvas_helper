from __future__ import annotations

from agent import AIAnalysisService, AIConfig
from backend.app.db import Database


def test_ai_service_has_no_canvas_token_or_client(tmp_path) -> None:
    service = AIAnalysisService(
        Database(tmp_path / "test.db"),
        AIConfig(base_url=None, api_key=None, model="local-fallback"),
    )

    assert not hasattr(service, "canvas")
    assert not hasattr(service, "canvas_client")
    assert not hasattr(service, "canvas_api_token")
