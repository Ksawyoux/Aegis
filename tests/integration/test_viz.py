"""Contract tests for the read-only visualization endpoints."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from aegis.api.app import create_app
from aegis.config import Settings


@pytest.fixture
def client(
    migrated_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setattr(
        "aegis.api.app.create_database_engine", lambda _settings: migrated_engine
    )
    with TestClient(create_app(Settings())) as test_client:
        yield test_client


def test_viz_serves_the_generated_dashboard(client: TestClient) -> None:
    response = client.get("/viz")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "How an alert becomes" in response.text


def test_live_snapshot_reports_counts_and_empty_feeds_on_a_fresh_schema(
    client: TestClient,
) -> None:
    response = client.get("/viz/live")

    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {
        "generated_at",
        "counts",
        "commits",
        "deployments",
        "infra_changes",
        "watermarks",
    }
    assert isinstance(body["counts"], dict)
    assert body["counts"]["commits"] == 0
    assert body["commits"] == []
