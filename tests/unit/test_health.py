# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""Smoke test: the app boots and /health answers."""

from fastapi.testclient import TestClient

from api.main import app


def test_health_answers_without_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "unconfigured"
