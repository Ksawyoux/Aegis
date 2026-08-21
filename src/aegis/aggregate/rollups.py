"""Delete-and-recompute maintenance for minute error rollups.

Callers own the transaction: capture the dirty set before changing base rows, perform
the base-row mutation, call :func:`recompute`, and advance any ingest watermark before
committing the same transaction.  ``recompute`` neither begins nor commits a transaction.

For base-row deletion, the required order is capture the dirty set, delete the citing
rollups, delete the log events, then recompute.  ``exemplar_log_event_id`` is
``ON DELETE RESTRICT`` deliberately, so deleting a cited event without first deleting
its rollup fails loudly.

The session-local temporary dirty-set table is created with ``ON COMMIT DROP``.  It is
therefore owned by the caller's transaction and is removed when that transaction ends;
within one transaction, each call clears it before loading its own dirty set.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeAlias

from sqlalchemy import select, text, tuple_
from sqlalchemy.orm import Session

from aegis.db.models import ErrorRollup

DirtyPair: TypeAlias = tuple[int, datetime]
"""A service id and its UTC, minute-aligned rollup bucket."""

DirtySet: TypeAlias = frozenset[DirtyPair]
"""All service-minute buckets that must be deleted and rebuilt."""


@dataclass(frozen=True)
class RollupReport:
    """The rows replaced by a :func:`recompute` invocation."""

    dirty_pairs: int
    deleted: int
    inserted: int


def capture_dirty_set(session: Session, *, changed: Iterable[DirtyPair]) -> DirtySet:
    """Capture dirty service-minutes *before* their base rows are changed.

    ``changed`` must include the old and new bucket for every update, and the bucket
    for every inserted or deleted event.  Existing rollup pairs are included while
    they are still visible, which makes a wholly deleted minute rebuildable.
    """
    changed_pairs = _normalise_dirty_pairs(changed)
    if not changed_pairs:
        return frozenset()

    existing_pairs = (
        session.execute(
            select(ErrorRollup.service_id, ErrorRollup.bucket_start).where(
                tuple_(ErrorRollup.service_id, ErrorRollup.bucket_start).in_(sorted(changed_pairs))
            )
        )
        .tuples()
        .all()
    )
    return frozenset(changed_pairs | _normalise_dirty_pairs(existing_pairs))


def delete_rollups(session: Session, *, dirty: DirtySet) -> int:
    """Delete the rollups covering ``dirty`` before their base rows are removed.

    ``error_rollups.exemplar_log_event_id`` is ``ON DELETE RESTRICT``, so any
    caller that deletes a ``log_events`` row must first remove the rollups that
    might cite it. A replaced row is frequently its own exemplar, which makes
    this the common case rather than the corner one, and the failure is a
    transaction-aborting IntegrityError rather than anything localised.

    ``recompute`` performs the same deletion, but it must run *after* the base
    rows change; this exists for the window before that.
    """
    dirty_pairs = _normalise_dirty_pairs(dirty)
    if not dirty_pairs:
        return 0
    _load_dirty_pairs(session, dirty_pairs)
    return len(
        session.execute(
            text(
                """
                DELETE FROM error_rollups AS rollup
                USING aegis_dirty_rollup_minutes AS dirty
                WHERE rollup.service_id = dirty.service_id
                  AND rollup.bucket_start = dirty.bucket_start
                RETURNING 1
                """
            )
        ).all()
    )


def recompute(session: Session, *, dirty: DirtySet) -> RollupReport:
    """Delete and exactly rebuild rollups for an explicit pre-captured dirty set.

    The caller must pass a set captured before its base-row mutation and must retain
    ownership of the surrounding transaction.  Locks are intentionally per service,
    rather than per ingest source, so concurrent sources touching one service-minute
    serialize before either one deletes or inserts a rollup primary key.
    """
    dirty_pairs = _normalise_dirty_pairs(dirty)
    if not dirty_pairs:
        return RollupReport(dirty_pairs=0, deleted=0, inserted=0)

    _load_dirty_pairs(session, dirty_pairs)

    # One set-based lock statement preserves ascending service-id acquisition order.
    session.execute(
        text(
            """
            SELECT pg_advisory_xact_lock(hashtext('rollup:' || service_id))
            FROM (
                SELECT service_id
                FROM aegis_dirty_rollup_minutes
                GROUP BY service_id
                ORDER BY service_id
            ) AS affected_services
            """
        )
    ).all()

    deleted = len(
        session.execute(
            text(
                """
                DELETE FROM error_rollups AS rollup
                USING aegis_dirty_rollup_minutes AS dirty
                WHERE rollup.service_id = dirty.service_id
                  AND rollup.bucket_start = dirty.bucket_start
                RETURNING 1
                """
            )
        ).all()
    )

    inserted = len(
        session.execute(
            text(
                """
                INSERT INTO error_rollups (
                    service_id, bucket_start, status_class, level, template_hash,
                    count, first_seen, last_seen, exemplar_log_event_id
                )
                SELECT
                    e.service_id,
                    date_trunc('minute', e.ts) AS bucket_start,
                    CASE
                        WHEN e.status_code BETWEEN 100 AND 599
                            THEN (e.status_code / 100)::text || 'xx'
                        ELSE 'none'
                    END AS status_class,
                    e.level,
                    e.template_hash,
                    count(*)::integer AS count,
                    min(e.ts) AS first_seen,
                    max(e.ts) AS last_seen,
                    -- Each term is COALESCEd because an absent attrs key makes
                    -- jsonb_typeof(...) NULL, and a single NULL would otherwise
                    -- propagate through the addition and null the whole score --
                    -- collapsing every row to the id tiebreak.
                    (array_agg(e.id ORDER BY
                       ( (e.trace_id IS NOT NULL AND e.trace_id <> '')::int
                       + COALESCE(jsonb_typeof(e.attrs->'stack')       = 'array',  false)::int
                       + COALESCE(jsonb_typeof(e.attrs->'upstream')    = 'string', false)::int
                       + COALESCE(jsonb_typeof(e.attrs->'duration_ms') = 'number', false)::int
                       + COALESCE(jsonb_typeof(e.attrs->'exc_type')    = 'string', false)::int
                       ) DESC,
                       e.id ASC))[1] AS exemplar_log_event_id
                FROM log_events AS e
                JOIN aegis_dirty_rollup_minutes AS dirty
                  ON e.service_id = dirty.service_id
                 AND date_trunc('minute', e.ts) = dirty.bucket_start
                GROUP BY e.service_id, date_trunc('minute', e.ts), status_class,
                         e.level, e.template_hash
                RETURNING 1
                """
            )
        ).all()
    )

    return RollupReport(dirty_pairs=len(dirty_pairs), deleted=deleted, inserted=inserted)


def _load_dirty_pairs(session: Session, dirty_pairs: DirtySet) -> None:
    """Replace this transaction's session-local dirty set."""
    session.execute(
        text(
            """
            CREATE TEMPORARY TABLE IF NOT EXISTS aegis_dirty_rollup_minutes (
                service_id integer NOT NULL,
                bucket_start timestamptz NOT NULL,
                PRIMARY KEY (service_id, bucket_start)
            ) ON COMMIT DROP
            """
        )
    )
    session.execute(text("DELETE FROM aegis_dirty_rollup_minutes"))
    session.execute(
        text(
            """
            INSERT INTO aegis_dirty_rollup_minutes (service_id, bucket_start)
            VALUES (:service_id, :bucket_start)
            """
        ),
        [
            {"service_id": service_id, "bucket_start": bucket_start}
            for service_id, bucket_start in sorted(dirty_pairs)
        ],
    )


def _normalise_dirty_pairs(pairs: Iterable[DirtyPair]) -> DirtySet:
    """Validate dirty pairs and render each bucket as a UTC minute boundary."""
    normalised: set[DirtyPair] = set()
    for service_id, bucket_start in pairs:
        if not isinstance(service_id, int) or isinstance(service_id, bool):
            raise ValueError("dirty service_id must be an integer")
        if bucket_start.tzinfo is None or bucket_start.utcoffset() is None:
            raise ValueError("dirty bucket_start must be timezone-aware")
        utc_bucket = bucket_start.astimezone(UTC)
        if utc_bucket.second != 0 or utc_bucket.microsecond != 0:
            raise ValueError("dirty bucket_start must be aligned to a minute")
        normalised.add((service_id, utc_bucket))
    return frozenset(normalised)
