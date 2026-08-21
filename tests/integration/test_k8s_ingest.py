# ruff: noqa: E501
from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from aegis.db.models import ErrorRollup, LogEvent, Service
from aegis.ingest.k8s import ingest_kubernetes
from aegis.ingest.normalize import ServiceRegistry


@pytest.fixture
def session(migrated_engine: Engine) -> Generator[Session]:
    connection = migrated_engine.connect()
    transaction = connection.begin()
    db_session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield db_session
    finally:
        db_session.close()
        transaction.rollback()
        connection.close()


def _registry(session: Session) -> ServiceRegistry:
    service = Service(name="search-api", k8s_names=["search-api"])
    session.add(service)
    session.flush()
    return ServiceRegistry.load([service])


def test_oomkilled_comes_from_pod_status_not_event_reason(session: Session, tmp_path: Path) -> None:
    source = tmp_path / "pod-status.json"
    source.write_text(json.dumps([{
        "metadata": {"uid": "pod-1", "name": "search-api"},
        "status": {"containerStatuses": [{"name": "worker", "restartCount": 4, "lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137, "finishedAt": "2026-08-19T14:00:00Z"}}}]},
    }]))

    assert ingest_kubernetes(session, path=source, registry=_registry(session)) == 1
    event = session.scalar(select(LogEvent))
    assert event is not None
    assert event.message == "OOMKilled: container worker terminated (exit 137)"
    assert event.attrs["restart_count"] == 4
    assert event.attrs["reason"] == "OOMKilled"


def test_k8s_event_count_is_one_row_with_occurrence_count(session: Session, tmp_path: Path) -> None:
    source = tmp_path / "events.json"
    source.write_text(json.dumps([{
        "metadata": {"uid": "event-1"},
        "involvedObject": {"name": "search-api"},
        "type": "Warning", "reason": "BackOff", "message": "wrong reason must not affect pod status",
        "count": 7, "lastTimestamp": "2026-08-19T14:00:00Z",
    }]))

    assert ingest_kubernetes(session, path=source, registry=_registry(session)) == 1
    events = session.scalars(select(LogEvent)).all()
    assert len(events) == 1
    assert events[0].attrs["occurrence_count"] == 7
    assert session.scalar(select(ErrorRollup.count)) == 1
