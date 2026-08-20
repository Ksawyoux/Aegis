from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from aegis.aggregate.rollups import RollupReport
from aegis.db.models import ErrorRollup, IngestWatermark, LogEvent, Service, UnresolvedEvent
from aegis.ingest.logs import ResolvedDraft, UnresolvedDraft
from aegis.ingest.pipeline import (
    CursorGap,
    CursorRegression,
    EvidenceConflict,
    EvidenceRegression,
    ingest_source,
)


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


def test_new_records_have_exclusive_report_counts_and_advance_watermark(session: Session) -> None:
    service = _service(session)
    source = _source()
    records = [
        _resolved(service.id, 0, "first"),
        _resolved(service.id, 5, "second", status_code=200),
        _unresolved(11, "third"),
    ]

    with session.begin_nested():
        report = ingest_source(session, source=source, records=records, cursor=_consumed(records))

    assert (report.inserted, report.duplicates, report.promoted, report.unresolved) == (2, 0, 0, 1)
    assert report.inserted + report.duplicates + report.promoted + report.unresolved == len(records)
    assert report.rollup == RollupReport(dirty_pairs=1, deleted=0, inserted=2)
    assert session.scalar(
        select(IngestWatermark.last_cursor).where(IngestWatermark.source == source)
    ) == str(_consumed(records))


def test_exact_replay_is_a_duplicate_noop_with_identical_rollup_contents(session: Session) -> None:
    service = _service(session)
    source = _source()
    records = [
        _resolved(service.id, 0, "first", trace_id="trace-1"),
        _resolved(service.id, 5, "second", attrs={"stack": ["frame"]}),
    ]
    cursor = _consumed(records)

    with session.begin_nested():
        ingest_source(session, source=source, records=records, cursor=cursor)
    before = _rollup_contents(session, service.name)

    with session.begin_nested():
        report = ingest_source(session, source=source, records=records, cursor=cursor)
    after = _rollup_contents(session, service.name)

    assert (report.inserted, report.duplicates, report.promoted, report.unresolved) == (0, 2, 0, 0)
    assert report.rollup == RollupReport(dirty_pairs=0, deleted=0, inserted=0)
    assert after == before


def test_same_uid_with_changed_resolved_content_raises_evidence_conflict(session: Session) -> None:
    service = _service(session)
    source = _source()
    original = _resolved(service.id, 0, "first")

    with session.begin_nested():
        ingest_source(session, source=source, records=[original], cursor=_consumed([original]))

    changed = _resolved(service.id, 0, "changed", uid=original.uid)
    with pytest.raises(EvidenceConflict) as raised:
        with session.begin_nested():
            ingest_source(session, source=source, records=[changed], cursor=_consumed([changed]))

    assert raised.value.uid == original.uid
    assert raised.value.differing_columns == ("message", "raw")


def test_unresolved_record_promotes_to_resolved_with_same_uid(session: Session) -> None:
    service = _service(session)
    source = _source()
    unresolved = _unresolved(0, "later-resolved")

    with session.begin_nested():
        ingest_source(session, source=source, records=[unresolved], cursor=_consumed([unresolved]))

    resolved = _resolved(service.id, 0, "later-resolved", uid=unresolved.uid)
    with session.begin_nested():
        report = ingest_source(
            session, source=source, records=[resolved], cursor=_consumed([resolved])
        )

    assert (report.inserted, report.duplicates, report.promoted, report.unresolved) == (0, 0, 1, 0)
    assert (
        session.scalar(select(LogEvent.uid).where(LogEvent.uid == unresolved.uid)) == unresolved.uid
    )
    assert (
        session.scalar(select(UnresolvedEvent.uid).where(UnresolvedEvent.uid == unresolved.uid))
        is None
    )


def test_resolved_record_that_becomes_unresolved_raises_evidence_regression(
    session: Session,
) -> None:
    service = _service(session)
    source = _source()
    resolved = _resolved(service.id, 0, "regression")

    with session.begin_nested():
        ingest_source(session, source=source, records=[resolved], cursor=_consumed([resolved]))

    regressed = _unresolved(0, "regression", uid=resolved.uid)
    with pytest.raises(EvidenceRegression, match=resolved.uid):
        with session.begin_nested():
            ingest_source(
                session, source=source, records=[regressed], cursor=_consumed([regressed])
            )


def test_cursor_regression_and_gap_raise(session: Session) -> None:
    service = _service(session)
    source = _source()
    record = _resolved(service.id, 0, "cursor")
    cursor = _consumed([record])

    with session.begin_nested():
        ingest_source(session, source=source, records=[record], cursor=cursor)

    with pytest.raises(CursorRegression):
        with session.begin_nested():
            ingest_source(session, source=source, records=[], cursor=cursor - 1)
    with pytest.raises(CursorGap):
        with session.begin_nested():
            ingest_source(session, source=_source(), records=[record], cursor=cursor + 1)


def test_failure_before_watermark_rolls_back_rows_rollups_and_watermark(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(session)
    source = _source()
    initial = _resolved(service.id, 0, "seed")
    with session.begin_nested():
        ingest_source(session, source=source, records=[initial], cursor=_consumed([initial]))
    before_rollups = _rollup_contents(session, service.name)
    before_cursor = _consumed([initial])
    record = _resolved(service.id, before_cursor, "atomic")
    next_cursor = before_cursor + _consumed([record])

    def fail_after_rows(_session: Session, *, dirty: object) -> RollupReport:
        raise RuntimeError("injected before watermark")

    monkeypatch.setattr("aegis.ingest.pipeline.recompute", fail_after_rows)
    with pytest.raises(RuntimeError, match="injected before watermark"):
        with session.begin_nested():
            ingest_source(session, source=source, records=[record], cursor=next_cursor)

    assert session.scalars(select(LogEvent.uid).order_by(LogEvent.uid)).all() == [initial.uid]
    assert _rollup_contents(session, service.name) == before_rollups
    assert session.scalar(
        select(IngestWatermark.last_cursor).where(IngestWatermark.source == source)
    ) == str(before_cursor)

    monkeypatch.undo()
    with session.begin_nested():
        report = ingest_source(session, source=source, records=[record], cursor=next_cursor)

    assert report.inserted == 1
    assert session.scalar(select(LogEvent.uid).where(LogEvent.uid == record.uid)) == record.uid
    assert session.scalar(
        select(IngestWatermark.last_cursor).where(IngestWatermark.source == source)
    ) == str(next_cursor)


def _service(session: Session) -> Service:
    service = Service(name=f"pipeline-{uuid4().hex}")
    session.add(service)
    session.flush()
    return service


def _source() -> str:
    return f"logs/pipeline-{uuid4().hex}.jsonl"


def _resolved(
    service_id: int,
    offset: int,
    raw: str,
    *,
    uid: str | None = None,
    status_code: int = 500,
    trace_id: str | None = None,
    attrs: dict[str, object] | None = None,
) -> ResolvedDraft:
    return ResolvedDraft(
        uid=uid or f"{offset + len(raw):032x}",
        ts=datetime(2025, 1, 1, 12, 0, tzinfo=UTC) + timedelta(seconds=offset),
        service_id=service_id,
        level="error",
        status_code=status_code,
        trace_id=trace_id,
        message=raw,
        template_hash=("a" if status_code == 500 else "b") * 32,
        raw=raw,
        attrs=attrs or {},
        source_file="logs/pipeline.jsonl",
        source_offset=offset,
    )


def _unresolved(offset: int, raw: str, *, uid: str | None = None) -> UnresolvedDraft:
    return UnresolvedDraft(
        uid=uid or f"{offset + len(raw):032x}",
        raw=raw,
        reason="no_service_match",
        source_file="logs/pipeline.jsonl",
        source_offset=offset,
    )


def _consumed(records: list[ResolvedDraft | UnresolvedDraft]) -> int:
    return sum(len(record.raw.encode("utf-8")) for record in records)


def _rollup_contents(session: Session, service_name: str) -> list[tuple[object, ...]]:
    return [
        (
            rollup.bucket_start,
            rollup.status_class,
            rollup.level,
            rollup.template_hash,
            rollup.count,
            rollup.first_seen,
            rollup.last_seen,
            event.uid,
            f"rollup:{service_name}/{rollup.bucket_start:%Y-%m-%dT%H:%M:%SZ}/"
            f"{rollup.status_class}/{rollup.level}/{rollup.template_hash}",
        )
        for rollup, event in session.execute(
            select(ErrorRollup, LogEvent)
            .join(LogEvent, LogEvent.id == ErrorRollup.exemplar_log_event_id)
            .where(
                ErrorRollup.service_id
                == session.scalar(select(Service.id).where(Service.name == service_name))
            )
            .order_by(
                ErrorRollup.bucket_start,
                ErrorRollup.status_class,
                ErrorRollup.level,
                ErrorRollup.template_hash,
            )
        )
    ]
