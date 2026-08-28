"""The push webhook schedules an evidence-backed code review of the landed commit.

TestClient executes BackgroundTasks synchronously after the response, so the
review row is assertable immediately. The GitHub diff fetch is monkeypatched:
these tests own the review pipeline, not the network.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Generator, Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from aegis.api.app import create_app
from aegis.config import Settings
from aegis.db.models import CodeReview, Service

_SECRET = "test-webhook-secret"
_CANNED_DIFF = (
    "diff --git a/pay.py b/pay.py\n"
    "--- a/pay.py\n"
    "+++ b/pay.py\n"
    "@@ -1,1 +1,2 @@\n"
    "+KEY = \"AKIAIOSFODNN7EXAMPLE\"\n"
)


def _sign(body: bytes) -> str:
    digest = hmac.new(_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.fixture
def registered_service(migrated_engine: Engine) -> Generator[tuple[str, str]]:
    name = f"review-{uuid4().hex[:8]}"
    repo = f"acme/{name}"
    with Session(migrated_engine) as session, session.begin():
        session.add(Service(name=name, repo=repo))
    try:
        yield name, repo
    finally:
        with Session(migrated_engine) as session, session.begin():
            session.query(CodeReview).filter_by(sha="b" * 40).delete()
            session.execute(
                text(
                    "DELETE FROM commits WHERE service_id = "
                    "(SELECT id FROM services WHERE name = :n)"
                ),
                {"n": name},
            )
            session.query(Service).filter_by(name=name).delete()


@pytest.fixture
def client(
    migrated_engine: Engine,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setattr(
        "aegis.review.service.fetch_commit_patch", lambda *a: _CANNED_DIFF
    )
    settings = Settings(database_url=database_url, github_webhook_secret=_SECRET)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _push(repo: str, sha: str) -> tuple[bytes, dict[str, str]]:
    payload: dict[str, Any] = {
        "ref": "refs/heads/main",
        "before": "0" * 40,
        "after": sha,
        "repository": {"full_name": repo},
        "pusher": {"name": "octocat"},
        "commits": [
            {
                "id": sha,
                "timestamp": "2026-08-20T10:00:00Z",
                "message": "add key",
                "author": {"name": "Octo Cat"},
                "added": ["pay.py"],
                "modified": [],
                "removed": [],
            }
        ],
        "head_commit": {"id": sha},
    }
    body = json.dumps(payload).encode("utf-8")
    return body, {
        "x-github-event": "push",
        "x-github-delivery": uuid4().hex,
        "x-hub-signature-256": _sign(body),
        "content-type": "application/json",
    }


def test_a_landed_commit_is_reviewed_and_verdict_persisted(
    client: TestClient, registered_service: tuple[str, str], migrated_engine: Engine
) -> None:
    name, repo = registered_service
    sha = "b" * 40
    body, headers = _push(repo, sha)

    response = client.post("/webhooks/github", content=body, headers=headers)

    assert response.status_code == 202
    with Session(migrated_engine) as session:
        review = session.scalar(select(CodeReview).where(CodeReview.sha == sha))
    assert review is not None
    assert review.source == "push"
    assert review.verdict == "fail"
    assert review.files_changed == 1
    finding = review.findings[0]
    assert finding["rule_id"] == "sec-aws-key"
    assert finding["path"] == "pay.py"


def test_reviews_endpoint_lists_the_persisted_review(
    client: TestClient, registered_service: tuple[str, str]
) -> None:
    name, repo = registered_service
    sha = "b" * 40
    body, headers = _push(repo, sha)

    client.post("/webhooks/github", content=body, headers=headers)

    response = client.get("/api/reviews")
    assert response.status_code == 200
    reviews = response.json()["reviews"]
    match = [r for r in reviews if r["sha"] == sha]
    assert len(match) == 1
    assert match[0]["service"] == name
    assert match[0]["verdict"] == "fail"


def test_dashboard_snapshot_includes_incidents_and_reviews(
    client: TestClient, registered_service: tuple[str, str]
) -> None:
    _, repo = registered_service
    body, headers = _push(repo, "b" * 40)

    client.post("/webhooks/github", content=body, headers=headers)

    response = client.get("/viz/dashboard")
    assert response.status_code == 200
    snapshot = response.json()
    assert any(r["verdict"] == "fail" for r in snapshot["reviews"])
