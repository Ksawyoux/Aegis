"""Event snapshot replacement must survive the rollup foreign key.

``error_rollups.exemplar_log_event_id`` is ``ON DELETE RESTRICT``. A replaced
Event is very often the exemplar its own rollup points at, so deleting the old
row before the citing rollup is gone aborts the whole transaction. Nothing in
the unit tests can see this: the constraint only exists in PostgreSQL.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session

from aegis.db.models import ErrorRollup, LogEvent, Service, UnresolvedEvent
from aegis.ingest.k8s import ingest_kubernetes
from aegis.ingest.normalize import ServiceRegistry

_POD = "search-api-7d9f"
_EVENT_UID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def _clean(migrated_engine: Engine) -> Generator[None]:
    def purge() -> None:
        with Session(migrated_engine) as session, session.begin():
            session.execute(text("TRUNCATE error_rollups, log_events, unresolved_events CASCADE"))
            session.execute(delete(UnresolvedEvent))
            session.execute(delete(Service).where(Service.name == "search-api"))

    purge()
    yield
    purge()


def _events_file(path: Path, count: int) -> Path:
    payload = [
        {
            "metadata": {"uid": _EVENT_UID},
            "involvedObject": {"name": _POD},
            "reason": "BackOff",
            "message": "Back-off restarting failed container",
            "type": "Warning",
            "count": count,
            "lastTimestamp": "2026-08-20T03:10:00Z",
        }
    ]
    target = path / "events.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def test_replacing_an_event_that_is_its_own_rollup_exemplar_succeeds(
    migrated_engine: Engine, tmp_path: Path
) -> None:
    with Session(migrated_engine) as session, session.begin():
        session.add(Service(name="search-api", k8s_names=[_POD]))

    with Session(migrated_engine) as session, session.begin():
        registry = ServiceRegistry.load(session.scalars(select(Service)).all())
        ingest_kubernetes(session, path=_events_file(tmp_path, 1), registry=registry)

    with Session(migrated_engine) as session:
        first = session.scalars(select(LogEvent)).one()
        exemplar = session.scalars(select(ErrorRollup.exemplar_log_event_id)).one()
        assert exemplar == first.id, "precondition: the event must be its own exemplar"

    # The replacement: same event_uid, higher count, so a new uid and a delete.
    with Session(migrated_engine) as session, session.begin():
        registry = ServiceRegistry.load(session.scalars(select(Service)).all())
        ingest_kubernetes(session, path=_events_file(tmp_path, 7), registry=registry)

    with Session(migrated_engine) as session:
        events = session.scalars(select(LogEvent)).all()
        assert len(events) == 1
        assert events[0].attrs["occurrence_count"] == 7
        rollup_exemplar = session.scalars(select(ErrorRollup.exemplar_log_event_id)).one()
        assert rollup_exemplar == events[0].id


def test_a_lower_count_snapshot_is_ignored_rather_than_applied(
    migrated_engine: Engine, tmp_path: Path
) -> None:
    """A Kubernetes Event count only rises for one event_uid.

    A lower count is therefore an older snapshot arriving late, and applying it
    would discard occurrences already recorded -- silently, since the row would
    still look well formed.
    """
    with Session(migrated_engine) as session, session.begin():
        session.add(Service(name="search-api", k8s_names=[_POD]))

    with Session(migrated_engine) as session, session.begin():
        registry = ServiceRegistry.load(session.scalars(select(Service)).all())
        ingest_kubernetes(session, path=_events_file(tmp_path, 7), registry=registry)

    with Session(migrated_engine) as session, session.begin():
        registry = ServiceRegistry.load(session.scalars(select(Service)).all())
        ingest_kubernetes(session, path=_events_file(tmp_path, 1), registry=registry)

    with Session(migrated_engine) as session:
        events = session.scalars(select(LogEvent)).all()
        assert len(events) == 1
        assert events[0].attrs["occurrence_count"] == 7
