"""The test that would falsify the v0.3 milestone claim.

Concurrent duplicate alerts must cause exactly one incident row and exactly one
scheduled run. A sequential loop proves nothing here: the first request commits
long before the second begins, so a check-then-insert would pass it. The
barrier is what makes the requests actually contend.

``run_incident`` is replaced by a recording double on purpose. Wiring the real
agent would require an Anthropic key, cost money per run, and take minutes --
and a race is far easier to see when the scheduled work does nothing.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from aegis.api.app import create_app
from aegis.config import Settings
from aegis.db.models import Incident

CONCURRENCY = 20
DEDUP_KEY = "pd-concurrency-probe-0001"


@pytest.fixture
def client(migrated_engine: Engine) -> Iterator[TestClient]:
    settings = Settings(database_url=os.environ["AEGIS_DATABASE_URL"])
    with TestClient(create_app(settings)) as test_client:
        yield test_client
    with Session(migrated_engine) as session, session.begin():
        session.execute(delete(Incident).where(Incident.dedup_key == DEDUP_KEY))


def _alert() -> dict[str, Any]:
    return {
        "service": "checkout-api",
        "alert_name": "HighErrorRate",
        "dedup_key": DEDUP_KEY,
        "fired_at": "2026-08-20T10:00:00Z",
        "payload": {"severity": "critical"},
        "window_start": "2026-08-20T09:00:00Z",
        "window_end": "2026-08-20T10:00:00Z",
    }


def test_concurrent_duplicate_alerts_cause_exactly_one_run(
    client: TestClient, migrated_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    scheduled: list[int] = []
    lock = threading.Lock()

    def record(incident_id: int, *_args: object, **_kwargs: object) -> None:
        with lock:
            scheduled.append(incident_id)

    monkeypatch.setattr("aegis.api.webhooks.run_incident", record)

    barrier = threading.Barrier(CONCURRENCY)
    responses: list[Any] = []
    errors: list[BaseException] = []

    def fire() -> None:
        try:
            barrier.wait(timeout=10)
            responses.append(client.post("/webhooks/alert", json=_alert()))
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            with lock:
                errors.append(exc)

    started = time.monotonic()
    threads = [threading.Thread(target=fire) for _ in range(CONCURRENCY)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    elapsed = time.monotonic() - started

    assert not errors, f"request threads raised: {errors[:3]}"
    assert len(responses) == CONCURRENCY
    assert all(response.status_code in {200, 202} for response in responses)

    # Pool exhaustion would surface as a timeout rather than as a dedup failure,
    # which reads as flakiness unless it fails loudly here.
    assert elapsed < 30, f"concurrent alerts took {elapsed:.1f}s -- suspect pool exhaustion"

    with Session(migrated_engine) as session:
        rows = session.scalar(
            select(func.count()).select_from(Incident).where(Incident.dedup_key == DEDUP_KEY)
        )
    assert rows == 1

    assert len(scheduled) == 1, f"expected exactly one scheduled run, got {len(scheduled)}"
    created = [r for r in responses if r.json()["created"]]
    assert len(created) == 1
    assert created[0].json()["incident_id"] == scheduled[0]
    assert all(r.json()["dedup_key"] == DEDUP_KEY for r in responses)
