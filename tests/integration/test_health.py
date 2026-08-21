from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from aegis.api.app import create_app
from aegis.config import Settings


def test_healthz_reports_healthy_database_and_embedding_configuration(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    monkeypatch.setattr("aegis.api.app.create_database_engine", lambda _settings: engine)
    app = create_app(Settings(OPENAI_API_KEY="configured"))

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "checks": {
            "database": {"ok": True, "detail": None},
            "embeddings": {"ok": True, "detail": None},
        },
    }


def test_healthz_reports_missing_embedding_configuration(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    monkeypatch.setattr("aegis.api.app.create_database_engine", lambda _settings: engine)
    app = create_app(Settings(openai_api_key=None))

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json()["checks"]["database"] == {"ok": True, "detail": None}
    assert response.json()["checks"]["embeddings"]["ok"] is False
