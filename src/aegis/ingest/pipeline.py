"""Caller-owned, atomic log ingestion.

``ingest_source`` requires an already-active transaction and deliberately neither
commits nor rolls it back.  It applies immutable-evidence conflict handling,
rebuilds affected rollups, and advances the source watermark in that one caller
transaction.

This is an append-only path, so it captures rollup dirtiness after inserting base
rows.  A future path that deletes log rows must instead follow Part 0 §8.4:
capture the dirty set, delete citing rollups, delete rows, then recompute.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeAlias

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aegis.aggregate.rollups import RollupReport, capture_dirty_set, recompute
from aegis.db.models import IngestWatermark, LogEvent, UnresolvedEvent
from aegis.ingest.logs import Draft, ResolvedDraft, UnresolvedDraft

LogRecord: TypeAlias = Draft
"""A parsed resolved or unresolved record accepted by :func:`ingest_source`."""

_LOG_CONTENT_COLUMNS = (
    "ts",
    "service_id",
    "level",
    "status_code",
    "trace_id",
    "message",
    "template_hash",
    "raw",
    "attrs",
    "source_file",
    "source_offset",
)
_UNRESOLVED_CONTENT_COLUMNS = ("ts", "raw", "reason", "source_file", "source_offset")


@dataclass(frozen=True)
class IngestReport:
    """Mutually exclusive classifications and rollup work for one source batch."""

    source: str
    inserted: int
    duplicates: int
    promoted: int
    unresolved: int
    rollup: RollupReport


class TransactionRequired(RuntimeError):
    """Raised when ingestion is invoked without a caller-owned transaction."""


class EvidenceConflict(ValueError):
    """A stable evidence UID was supplied with different immutable content."""

    def __init__(self, uid: str, differing_columns: tuple[str, ...]) -> None:
        self.uid = uid
        self.differing_columns = differing_columns
        super().__init__(
            f"conflicting immutable evidence for {uid}: {', '.join(differing_columns)}"
        )


class EvidenceRegression(ValueError):
    """A previously resolved log UID was later supplied as unresolved."""

    def __init__(self, uid: str) -> None:
        self.uid = uid
        super().__init__(f"resolved evidence regressed to unresolved for {uid}")


class CursorRegression(ValueError):
    """The requested next-unread byte offset precedes the stored watermark."""

    def __init__(self, source: str, cursor: int, stored_cursor: int) -> None:
        self.source = source
        self.cursor = cursor
        self.stored_cursor = stored_cursor
        super().__init__(f"cursor regression for {source}: {cursor} < {stored_cursor}")


class CursorGap(ValueError):
    """The requested cursor skips bytes not represented by this ingest batch."""

    def __init__(self, source: str, cursor: int, maximum_cursor: int) -> None:
        self.source = source
        self.cursor = cursor
        self.maximum_cursor = maximum_cursor
        super().__init__(f"cursor gap for {source}: {cursor} > {maximum_cursor}")


def ingest_source(
    session: Session,
    *,
    source: str,
    records: Iterable[LogRecord],
    cursor: int,
) -> IngestReport:
    """Ingest parsed records in the caller's active transaction.

    ``cursor`` is the exclusive next-unread byte offset.  This function never
    calls ``commit`` or ``rollback``: any exception is intentionally left for the
    caller's transaction context manager to roll back.
    """
    if not session.in_transaction():
        raise TransactionRequired("ingest_source requires an active caller-owned transaction")
    _validate_cursor(cursor)
    batch = tuple(records)

    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('ingest:' || :source))"), {"source": source}
    )
    stored_cursor = _stored_cursor(session, source)
    if cursor < stored_cursor:
        raise CursorRegression(source, cursor, stored_cursor)

    maximum_cursor = stored_cursor + _consumed_bytes(batch)
    if cursor > maximum_cursor:
        raise CursorGap(source, cursor, maximum_cursor)

    inserted = 0
    duplicates = 0
    promoted = 0
    unresolved = 0
    changed: set[tuple[int, datetime]] = set()

    for record in batch:
        if isinstance(record, ResolvedDraft):
            outcome = _ingest_resolved(session, record)
            if outcome == "inserted":
                inserted += 1
                changed.add((record.service_id, _minute(record.ts)))
            elif outcome == "promoted":
                promoted += 1
                changed.add((record.service_id, _minute(record.ts)))
            else:
                duplicates += 1
        else:
            outcome = _ingest_unresolved(session, record)
            if outcome == "unresolved":
                unresolved += 1
            else:
                duplicates += 1

    assert inserted + duplicates + promoted + unresolved == len(batch)
    session.flush()
    dirty = capture_dirty_set(session, changed=changed)
    rollup = recompute(session, dirty=dirty)
    _upsert_watermark(session, source, cursor)

    return IngestReport(
        source=source,
        inserted=inserted,
        duplicates=duplicates,
        promoted=promoted,
        unresolved=unresolved,
        rollup=rollup,
    )


def _ingest_resolved(session: Session, record: ResolvedDraft) -> str:
    existing_log = session.scalar(
        select(LogEvent).where(LogEvent.uid == record.uid).with_for_update()
    )
    if existing_log is not None:
        differing = _differing_columns(existing_log, record, _LOG_CONTENT_COLUMNS)
        if differing:
            raise EvidenceConflict(record.uid, differing)
        return "duplicate"

    existing_unresolved = session.scalar(
        select(UnresolvedEvent).where(UnresolvedEvent.uid == record.uid).with_for_update()
    )
    if existing_unresolved is not None:
        session.add(_log_event(record))
        session.delete(existing_unresolved)
        return "promoted"

    session.add(_log_event(record))
    return "inserted"


def _ingest_unresolved(session: Session, record: UnresolvedDraft) -> str:
    existing_log = session.scalar(
        select(LogEvent).where(LogEvent.uid == record.uid).with_for_update()
    )
    if existing_log is not None:
        raise EvidenceRegression(record.uid)

    existing_unresolved = session.scalar(
        select(UnresolvedEvent).where(UnresolvedEvent.uid == record.uid).with_for_update()
    )
    if existing_unresolved is not None:
        differing = _differing_columns(existing_unresolved, record, _UNRESOLVED_CONTENT_COLUMNS)
        if differing:
            raise EvidenceConflict(record.uid, differing)
        return "duplicate"

    session.add(
        UnresolvedEvent(
            uid=record.uid,
            ts=record.ts,
            raw=record.raw,
            reason=record.reason,
            source_file=record.source_file,
            source_offset=record.source_offset,
        )
    )
    return "unresolved"


def _log_event(record: ResolvedDraft) -> LogEvent:
    return LogEvent(
        uid=record.uid,
        ts=record.ts,
        service_id=record.service_id,
        level=record.level,
        status_code=record.status_code,
        trace_id=record.trace_id,
        message=record.message,
        template_hash=record.template_hash,
        raw=record.raw,
        attrs=record.attrs,
        source_file=record.source_file,
        source_offset=record.source_offset,
    )


def _differing_columns(
    existing: LogEvent | UnresolvedEvent,
    record: LogRecord,
    columns: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(
        column for column in columns if getattr(existing, column) != getattr(record, column)
    )


def _minute(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("resolved log timestamps must be timezone-aware")
    return timestamp.astimezone(UTC).replace(second=0, microsecond=0)


def _stored_cursor(session: Session, source: str) -> int:
    watermark = session.scalar(
        select(IngestWatermark).where(IngestWatermark.source == source).with_for_update()
    )
    if watermark is None:
        return 0
    try:
        stored_cursor = int(watermark.last_cursor)
    except ValueError as error:
        raise ValueError(
            f"invalid stored cursor for {source}: {watermark.last_cursor!r}"
        ) from error
    _validate_cursor(stored_cursor)
    return stored_cursor


def _consumed_bytes(records: tuple[LogRecord, ...]) -> int:
    """Return draft-visible consumed bytes.

    ``Draft.raw`` is normalized by the parser and consequently does not retain a
    line terminator.  The pipeline therefore measures the byte payload exposed by
    its record interface; callers that read a physical file pass matching cursor
    batches rather than inventing missing offsets.
    """
    return sum(len(record.raw.encode("utf-8")) for record in records)


def _upsert_watermark(session: Session, source: str, cursor: int) -> None:
    statement = insert(IngestWatermark).values(
        source=source,
        last_cursor=str(cursor),
        updated_at=func.now(),
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[IngestWatermark.source],
            set_={"last_cursor": str(cursor), "updated_at": func.now()},
        )
    )


def _validate_cursor(cursor: int) -> None:
    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
        raise ValueError("cursor must be a non-negative integer")


__all__ = [
    "CursorGap",
    "CursorRegression",
    "EvidenceConflict",
    "EvidenceRegression",
    "IngestReport",
    "LogRecord",
    "TransactionRequired",
    "ingest_source",
]
