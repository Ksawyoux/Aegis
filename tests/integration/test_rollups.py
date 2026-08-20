from __future__ import annotations

from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aegis.aggregate.rollups import capture_dirty_set, recompute
from aegis.db.models import ErrorRollup, LogEvent, Service


@pytest.fixture
def session(migrated_engine: Engine) -> Generator[Session]:
    # Each test rolls back its own data; the engine fixture performs the PostgreSQL skip.
    connection = migrated_engine.connect()
    transaction = connection.begin()
    db_session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield db_session
    finally:
        db_session.close()
        transaction.rollback()
        connection.close()


def test_wholly_empty_minute_rollup_deleted(session: Session) -> None:
    service = _service(session)
    bucket = _bucket()
    event = _event(session, service.id, bucket + timedelta(seconds=5), "empty-minute")
    session.flush()
    dirty = capture_dirty_set(session, changed={(service.id, bucket)})
    recompute(session, dirty=dirty)
    assert _rollups(session, service.id) != []

    # This is the required deletion order for a cited exemplar.
    session.execute(
        delete(ErrorRollup).where(
            ErrorRollup.service_id == service.id,
            ErrorRollup.bucket_start == bucket,
        )
    )
    session.delete(event)
    session.flush()
    recompute(session, dirty=dirty)

    assert _rollups(session, service.id) == []


def test_exemplar_ignores_json_null_valued_keys(session: Session) -> None:
    service = _service(session)
    bucket = _bucket()
    empty = _event(
        session,
        service.id,
        bucket + timedelta(seconds=1),
        "null-evidence",
        attrs={
            "stack": None,
            "upstream": None,
            "duration_ms": None,
            "exc_type": None,
        },
    )
    rich = _event(
        session,
        service.id,
        bucket + timedelta(seconds=2),
        "real-stack",
        attrs={"stack": ["traceback"]},
    )
    session.flush()

    recompute(session, dirty=capture_dirty_set(session, changed={(service.id, bucket)}))

    assert _rollups(session, service.id)[0].exemplar_log_event_id == rich.id
    assert empty.id < rich.id


def test_exemplar_ignores_empty_trace_id(session: Session) -> None:
    service = _service(session)
    bucket = _bucket()
    empty_trace = _event(
        session, service.id, bucket + timedelta(seconds=1), "empty-trace", trace_id=""
    )
    trace = _event(
        session, service.id, bucket + timedelta(seconds=2), "trace", trace_id="trace-123"
    )
    session.flush()

    recompute(session, dirty=capture_dirty_set(session, changed={(service.id, bucket)}))

    assert _rollups(session, service.id)[0].exemplar_log_event_id == trace.id
    assert empty_trace.id < trace.id


def test_two_sources_same_service_minute_do_not_deadlock_or_conflict(
    migrated_engine: Engine,
) -> None:
    bucket = _bucket()
    with Session(migrated_engine) as setup:
        service = _service(setup)
        service_id = service.id
        setup.commit()

    both_base_rows_written = Barrier(2)

    def ingest(source: str) -> None:
        with Session(migrated_engine) as worker:
            with worker.begin():
                _event(worker, service_id, bucket + timedelta(seconds=10), source)
                worker.flush()
                both_base_rows_written.wait(timeout=10)
                recompute(worker, dirty=frozenset({(service_id, bucket)}))

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(ingest, f"source-{number}") for number in range(2)]
            for future in futures:
                future.result(timeout=10)

        with Session(migrated_engine) as check:
            rollup = check.scalars(
                select(ErrorRollup).where(ErrorRollup.service_id == service_id)
            ).one()
            assert rollup.count == 2
    finally:
        with Session(migrated_engine) as cleanup:
            with cleanup.begin():
                cleanup.execute(delete(ErrorRollup).where(ErrorRollup.service_id == service_id))
                cleanup.execute(delete(LogEvent).where(LogEvent.service_id == service_id))
                cleanup.execute(delete(Service).where(Service.id == service_id))


def test_deleting_cited_exemplar_raises_fk_violation(session: Session) -> None:
    service = _service(session)
    bucket = _bucket()
    event = _event(session, service.id, bucket + timedelta(seconds=1), "cited")
    session.flush()
    recompute(session, dirty=capture_dirty_set(session, changed={(service.id, bucket)}))

    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.execute(delete(LogEvent).where(LogEvent.id == event.id))


def test_crash_after_base_rows_before_rollups_leaves_no_gap(migrated_engine: Engine) -> None:
    bucket = _bucket()
    uid = f"crash-{uuid4().hex}"
    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            service = _service(session)
            session.flush()
            _event(session, service.id, bucket + timedelta(seconds=1), uid, uid=uid)
            session.flush()
        finally:
            session.close()
            # A crash before recompute aborts the caller-owned transaction, including base rows.
            transaction.rollback()

    with Session(migrated_engine) as check:
        assert check.scalar(select(LogEvent.id).where(LogEvent.uid == uid)) is None


def test_overlapping_recompute_does_not_undercount(session: Session) -> None:
    service = _service(session)
    bucket = _bucket()
    _event(session, service.id, bucket + timedelta(seconds=1), "first")
    session.flush()
    dirty = capture_dirty_set(session, changed={(service.id, bucket)})
    recompute(session, dirty=dirty)

    _event(session, service.id, bucket + timedelta(seconds=2), "second")
    session.flush()
    recompute(session, dirty=capture_dirty_set(session, changed={(service.id, bucket)}))
    recompute(session, dirty=dirty)

    assert _rollups(session, service.id)[0].count == 2


def test_late_earlier_event_updates_first_seen(session: Session) -> None:
    service = _service(session)
    bucket = _bucket()
    later = bucket + timedelta(seconds=50)
    earlier = bucket + timedelta(seconds=5)
    _event(session, service.id, later, "later")
    session.flush()
    dirty = capture_dirty_set(session, changed={(service.id, bucket)})
    recompute(session, dirty=dirty)

    _event(session, service.id, earlier, "earlier")
    session.flush()
    recompute(session, dirty=capture_dirty_set(session, changed={(service.id, bucket)}))

    rollup = _rollups(session, service.id)[0]
    assert rollup.count == 2
    assert rollup.first_seen == earlier


def _bucket() -> datetime:
    return datetime(2025, 1, 1, 12, 0, tzinfo=UTC)


def _service(session: Session) -> Service:
    service = Service(name=f"rollups-{uuid4().hex}")
    session.add(service)
    session.flush()
    return service


def _event(
    session: Session,
    service_id: int,
    timestamp: datetime,
    label: str,
    *,
    attrs: dict[str, object] | None = None,
    trace_id: str | None = None,
    uid: str | None = None,
) -> LogEvent:
    event = LogEvent(
        uid=uid or f"event-{label}-{uuid4().hex}",
        ts=timestamp,
        service_id=service_id,
        level="error",
        status_code=500,
        trace_id=trace_id,
        message=label,
        template_hash="a" * 32,
        raw=label,
        attrs=attrs or {},
        source_file=label,
        source_offset=0,
    )
    session.add(event)
    return event


def _rollups(session: Session, service_id: int) -> list[ErrorRollup]:
    return list(
        session.scalars(
            select(ErrorRollup)
            .where(ErrorRollup.service_id == service_id)
            .order_by(ErrorRollup.first_seen)
        )
    )
