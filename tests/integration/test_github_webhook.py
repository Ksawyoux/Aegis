"""Composition tests for ``POST /webhooks/github`` (Part 4 §5.6).

These exercise the real app, a real database transaction, real registry
resolution, the existing Git ingest adapter, and -- for the final test -- a
real spawned-stdio MCP tool call. Unit-testing the HMAC helper and the
payload adapter in isolation would not catch a defect in how they compose,
which is exactly the kind of gap the rest of this milestone's tests exist to
close.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from collections.abc import Generator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from aegis.api.app import create_app
from aegis.config import Settings
from aegis.db.models import Service

_SECRET = "test-webhook-secret"


def _sign(body: bytes, secret: str = _SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _headers(
    body: bytes, *, event: str, secret: str | None = _SECRET, sign: bool = True
) -> dict[str, str]:
    headers = {"x-github-event": event, "x-github-delivery": uuid4().hex}
    if sign and secret is not None:
        headers["x-hub-signature-256"] = _sign(body, secret)
    return headers


@pytest.fixture
def registered_service(migrated_engine: Engine) -> Generator[tuple[str, str]]:
    """A service registered to a repo, so push/PR payloads resolve to it."""
    name = f"github-webhook-{uuid4().hex[:8]}"
    repo = f"acme/{name}"
    with Session(migrated_engine) as session, session.begin():
        session.add(Service(name=name, repo=repo))
    try:
        yield name, repo
    finally:
        with Session(migrated_engine) as session, session.begin():
            session.execute(
                text(
                    "DELETE FROM commits WHERE service_id = "
                    "(SELECT id FROM services WHERE name = :n)"
                ),
                {"n": name},
            )
            session.execute(text("DELETE FROM services WHERE name = :n"), {"n": name})


@pytest.fixture
def client(migrated_engine: Engine, database_url: str) -> Iterator[TestClient]:
    # Built through the production factory on purpose: an earlier revision
    # mounted the router by hand here, so these tests passed while create_app
    # exposed no /webhooks/github route at all.
    settings = Settings(database_url=database_url, github_webhook_secret=_SECRET)
    with TestClient(create_app(settings)) as client:
        yield client


def _push_payload(
    repo: str, *, sha: str, paths: dict[str, list[str]] | None = None
) -> dict[str, Any]:
    changed = paths or {"added": ["src/app.py"], "modified": [], "removed": []}
    return {
        "ref": "refs/heads/main",
        "before": "0" * 40,
        "after": sha,
        "repository": {"full_name": repo},
        "pusher": {"name": "octocat"},
        "commits": [
            {
                "id": sha,
                "timestamp": "2026-08-20T10:00:00Z",
                "message": "a change",
                "author": {"name": "Octo Cat"},
                **changed,
            }
        ],
        "head_commit": {"id": sha},
    }


def _sha(seed: str) -> str:
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()


class TestSecretAndSignature:
    def test_missing_secret_returns_503_and_touches_nothing(
        self, migrated_engine: Engine, database_url: str
    ) -> None:
        settings = Settings(database_url=database_url, github_webhook_secret=None)
        with TestClient(create_app(settings)) as client:
            response = client.post(
                "/webhooks/github", content=b'{"malformed', headers={"x-github-event": "push"}
            )

        assert response.status_code == 503

    def test_missing_signature_returns_401(
        self, client: TestClient, registered_service: tuple[str, str]
    ) -> None:
        _, repo = registered_service
        body = json.dumps(_push_payload(repo, sha=_sha("a"))).encode("utf-8")

        response = client.post(
            "/webhooks/github", content=body, headers=_headers(body, event="push", sign=False)
        )

        assert response.status_code == 401

    def test_bad_signature_returns_401(
        self, client: TestClient, registered_service: tuple[str, str]
    ) -> None:
        _, repo = registered_service
        body = json.dumps(_push_payload(repo, sha=_sha("a"))).encode("utf-8")
        headers = _headers(body, event="push")
        headers["x-hub-signature-256"] = "sha256=" + "0" * 64

        response = client.post("/webhooks/github", content=body, headers=headers)

        assert response.status_code == 401

    def test_signature_of_a_reserialized_body_is_rejected(
        self, client: TestClient, registered_service: tuple[str, str]
    ) -> None:
        """Whitespace is part of the signed bytes; a re-serialized copy must not verify."""
        _, repo = registered_service
        payload = _push_payload(repo, sha=_sha("a"))
        original_body = json.dumps(payload, indent=2).encode("utf-8")
        reserialized_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        assert original_body != reserialized_body
        signature = _sign(original_body)

        response = client.post(
            "/webhooks/github",
            content=reserialized_body,
            headers={"x-github-event": "push", "x-hub-signature-256": signature},
        )

        assert response.status_code == 401

    def test_signature_of_the_original_whitespace_sensitive_body_is_accepted(
        self, client: TestClient, registered_service: tuple[str, str]
    ) -> None:
        _, repo = registered_service
        payload = _push_payload(repo, sha=_sha("a"))
        body = json.dumps(payload, indent=2).encode("utf-8")

        response = client.post(
            "/webhooks/github", content=body, headers=_headers(body, event="push")
        )

        assert response.status_code == 202


class TestEventHandling:
    def test_signed_malformed_json_returns_400(self, client: TestClient) -> None:
        body = b"{not valid json"

        response = client.post(
            "/webhooks/github", content=body, headers=_headers(body, event="push")
        )

        assert response.status_code == 400

    def test_signed_ping_returns_202_with_no_rows(
        self, client: TestClient, migrated_engine: Engine, registered_service: tuple[str, str]
    ) -> None:
        body = json.dumps({"zen": "hello"}).encode("utf-8")

        response = client.post(
            "/webhooks/github", content=body, headers=_headers(body, event="ping")
        )

        assert response.status_code == 202
        with Session(migrated_engine) as session:
            count = session.execute(text("SELECT count(*) FROM commits")).scalar_one()
        assert count == 0

    def test_unknown_repo_returns_422_with_no_rows(
        self, client: TestClient, migrated_engine: Engine
    ) -> None:
        body = json.dumps(_push_payload("nobody/nowhere", sha=_sha("unknown"))).encode("utf-8")

        response = client.post(
            "/webhooks/github", content=body, headers=_headers(body, event="push")
        )

        assert response.status_code == 422
        with Session(migrated_engine) as session:
            found = session.execute(
                text("SELECT count(*) FROM commits WHERE sha = :sha"), {"sha": _sha("unknown")}
            ).scalar_one()
        assert found == 0


class TestPushIngest:
    def test_malformed_second_commit_yields_zero_inserts_for_the_whole_payload(
        self, client: TestClient, migrated_engine: Engine, registered_service: tuple[str, str]
    ) -> None:
        _, repo = registered_service
        good_sha = _sha("good")
        payload = {
            "ref": "refs/heads/main",
            "repository": {"full_name": repo},
            "commits": [
                {
                    "id": good_sha,
                    "timestamp": "2026-08-20T10:00:00Z",
                    "message": "ok",
                    "author": {"name": "A"},
                    "added": ["a.py"],
                    "modified": [],
                    "removed": [],
                },
                {
                    "id": "not-a-valid-sha",
                    "timestamp": "2026-08-20T10:01:00Z",
                    "message": "bad",
                    "author": {"name": "B"},
                    "added": ["b.py"],
                    "modified": [],
                    "removed": [],
                },
            ],
        }
        body = json.dumps(payload).encode("utf-8")

        response = client.post(
            "/webhooks/github", content=body, headers=_headers(body, event="push")
        )

        assert response.status_code == 422
        with Session(migrated_engine) as session:
            found = session.execute(
                text("SELECT count(*) FROM commits WHERE sha = :sha"), {"sha": good_sha}
            ).scalar_one()
        assert found == 0

    def test_replay_returns_unchanged_counts_and_one_commit_row(
        self, client: TestClient, migrated_engine: Engine, registered_service: tuple[str, str]
    ) -> None:
        _, repo = registered_service
        sha = _sha("replay")
        body = json.dumps(_push_payload(repo, sha=sha)).encode("utf-8")

        first = client.post("/webhooks/github", content=body, headers=_headers(body, event="push"))
        second = client.post("/webhooks/github", content=body, headers=_headers(body, event="push"))

        assert first.status_code == 202
        assert first.json()["inserted"] == 1
        assert second.status_code == 202
        assert second.json()["unchanged"] == 1
        with Session(migrated_engine) as session:
            found = session.execute(
                text("SELECT count(*) FROM commits WHERE sha = :sha"), {"sha": sha}
            ).scalar_one()
        assert found == 1

    def test_more_than_hunk_max_files_still_yields_webhook_omission_for_every_path(
        self, client: TestClient, migrated_engine: Engine, registered_service: tuple[str, str]
    ) -> None:
        _, repo = registered_service
        sha = _sha("many-files")
        paths = {"added": [f"file-{i:02d}.py" for i in range(20)], "modified": [], "removed": []}
        body = json.dumps(_push_payload(repo, sha=sha, paths=paths)).encode("utf-8")

        response = client.post(
            "/webhooks/github", content=body, headers=_headers(body, event="push")
        )

        assert response.status_code == 202
        with Session(migrated_engine) as session:
            files_changed = session.execute(
                text("SELECT files_changed FROM commits WHERE sha = :sha"), {"sha": sha}
            ).scalar_one()
        assert len(files_changed) == 20
        assert all(f["hunks"] is None for f in files_changed)
        assert all(f["hunks_omitted"] == "webhook" for f in files_changed)

    @pytest.mark.asyncio
    async def test_a_pushed_commit_is_reachable_through_spawned_stdio_get_incident_diff(
        self, client: TestClient, migrated_engine: Engine, registered_service: tuple[str, str]
    ) -> None:
        service_name, repo = registered_service
        sha = _sha("mcp-visible")
        body = json.dumps(_push_payload(repo, sha=sha)).encode("utf-8")

        response = client.post(
            "/webhooks/github", content=body, headers=_headers(body, event="push")
        )
        assert response.status_code == 202

        committed_at = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "aegis.mcp_server"],
            cwd=Path(__file__).parents[2],
            env={"AEGIS_DATABASE_URL": Settings().database_url},
        )
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "get_incident_diff",
                    {
                        "service": service_name,
                        "window_start": committed_at.isoformat().replace("+00:00", "Z"),
                        "window_end": (committed_at + timedelta(minutes=1))
                        .isoformat()
                        .replace("+00:00", "Z"),
                    },
                )

        assert result.isError is False
        rendered = json.dumps(result.structuredContent)
        assert f"commit:{sha}" in rendered
        assert "src/app.py" in rendered


class TestPullRequestEnrichment:
    def _insert_commit(self, engine: Engine, repo: str, sha: str) -> int:
        with Session(engine) as session, session.begin():
            service_id = session.execute(
                text("SELECT id FROM services WHERE repo = :repo"), {"repo": repo}
            ).scalar_one()
            session.execute(
                text(
                    """
                    INSERT INTO commits
                        (sha, service_id, authored_at, committed_at, message, author,
                         pr_number, files_changed, additions, deletions)
                    VALUES
                        (:sha, :service_id, now(), now(), 'm', 'a', NULL, '[]'::jsonb, 0, 0)
                    """
                ),
                {"sha": sha, "service_id": service_id},
            )
            return int(service_id)

    def _pr_payload(self, repo: str, *, number: int, head_sha: str) -> dict[str, Any]:
        return {
            "action": "opened",
            "number": number,
            "repository": {"full_name": repo},
            "pull_request": {
                "number": number,
                "head": {"sha": head_sha},
                "merge_commit_sha": None,
            },
        }

    def test_enrichment_is_idempotent(
        self, client: TestClient, migrated_engine: Engine, registered_service: tuple[str, str]
    ) -> None:
        _, repo = registered_service
        sha = _sha("pr-target")
        self._insert_commit(migrated_engine, repo, sha)
        body = json.dumps(self._pr_payload(repo, number=42, head_sha=sha)).encode("utf-8")

        first = client.post(
            "/webhooks/github", content=body, headers=_headers(body, event="pull_request")
        )
        second = client.post(
            "/webhooks/github", content=body, headers=_headers(body, event="pull_request")
        )

        assert first.status_code == 202
        assert second.status_code == 202
        with Session(migrated_engine) as session:
            pr_number = session.execute(
                text("SELECT pr_number FROM commits WHERE sha = :sha"), {"sha": sha}
            ).scalar_one()
        assert pr_number == 42

    def test_pr_before_push_reports_deferral_and_inserts_nothing(
        self, client: TestClient, migrated_engine: Engine, registered_service: tuple[str, str]
    ) -> None:
        _, repo = registered_service
        sha = _sha("never-pushed")
        body = json.dumps(self._pr_payload(repo, number=7, head_sha=sha)).encode("utf-8")

        response = client.post(
            "/webhooks/github", content=body, headers=_headers(body, event="pull_request")
        )

        assert response.status_code == 202
        assert response.json()["deferred_shas"] == [sha]
        with Session(migrated_engine) as session:
            found = session.execute(
                text("SELECT count(*) FROM commits WHERE sha = :sha"), {"sha": sha}
            ).scalar_one()
        assert found == 0

    def test_conflicting_pr_metadata_rolls_back_the_whole_enrichment(
        self, client: TestClient, migrated_engine: Engine, registered_service: tuple[str, str]
    ) -> None:
        _, repo = registered_service
        conflicting_sha = _sha("conflict-head")
        clean_sha = _sha("conflict-merge")
        self._insert_commit(migrated_engine, repo, conflicting_sha)
        self._insert_commit(migrated_engine, repo, clean_sha)
        with Session(migrated_engine) as session, session.begin():
            session.execute(
                text("UPDATE commits SET pr_number = 99 WHERE sha = :sha"),
                {"sha": conflicting_sha},
            )
        payload = {
            "action": "opened",
            "number": 100,
            "repository": {"full_name": repo},
            "pull_request": {
                "number": 100,
                "head": {"sha": conflicting_sha},
                "merge_commit_sha": clean_sha,
            },
        }
        body = json.dumps(payload).encode("utf-8")

        response = client.post(
            "/webhooks/github", content=body, headers=_headers(body, event="pull_request")
        )

        assert response.status_code == 409
        with Session(migrated_engine) as session:
            rows = session.execute(
                text("SELECT sha, pr_number FROM commits WHERE sha IN (:a, :b)"),
                {"a": conflicting_sha, "b": clean_sha},
            ).all()
        by_sha = dict(rows)
        assert by_sha[conflicting_sha] == 99
        assert by_sha[clean_sha] is None
