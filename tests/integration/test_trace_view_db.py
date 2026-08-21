"""Database-backed tests for ``load_stored_run`` (Part 4 §7.2).

The not-found and duplicate-run_id outcomes are detected by the SQL lookup
alone and are exercised for real here. Only the successful, single-match path
needs Part 3's ``IncidentRecord`` envelope model to parse the stored JSON, and
that module is not present in this tree yet -- that one test is skipped with
an explicit reason instead of the whole module.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Generator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

_records_present = importlib.util.find_spec("aegis.app.records") is not None

_DEDUP_KEYS = ("dedup-unique", "dedup-dup-1", "dedup-dup-2")


@pytest.fixture(autouse=True)
def _clean_incidents(migrated_engine: Engine) -> Generator[None]:
    """Remove this module's rows before and after each test.

    Without this the inserts collide with their own leftovers on a second run,
    surfacing as a UniqueViolation on dedup_key that looks nothing like a
    test-isolation problem.
    """
    def purge() -> None:
        with Session(migrated_engine) as session, session.begin():
            session.execute(
                text("DELETE FROM incidents WHERE dedup_key = ANY(:keys)"),
                {"keys": list(_DEDUP_KEYS)},
            )

    purge()
    yield
    purge()



@pytest.mark.skipif(
    not _records_present,
    reason=(
        "aegis.app.records (Part 3's IncidentRecord envelope) is not present in this "
        "tree; the successful load path cannot parse a stored envelope until Part 3 lands."
    ),
)
def test_load_stored_run_returns_the_one_matching_incident(migrated_engine: Engine) -> None:
    from aegis.agent.trace_view import load_stored_run

    run_id = "trace-view-unique-run"
    with Session(migrated_engine) as session, session.begin():
        session.execute(
            text(
                """
                INSERT INTO incidents
                    (dedup_key, opened_at, window_start, window_end, alert_payload, status,
                     summary_json)
                VALUES
                    ('dedup-unique', now(), now(), now(), '{}'::jsonb, 'investigating',
                     :envelope)
                """
            ),
            # json.dumps rather than a hand-built literal: the previous version
            # concatenated an f-string with a plain string, so the plain half's
            # "}}" stayed literal and the envelope was invalid JSON.
            {
                "envelope": json.dumps(
                    {"run_id": run_id, "summary": None, "trace": [], "delivery": None}
                )
            },
        )

    with Session(migrated_engine) as session:
        run = load_stored_run(session, run_id=run_id)

    assert run.record.run_id == run_id
    assert run.incident_status == "investigating"


def test_load_stored_run_raises_not_found_for_an_unknown_run_id(migrated_engine: Engine) -> None:
    from aegis.agent.trace_view import RunNotFoundError, load_stored_run

    with Session(migrated_engine) as session, pytest.raises(RunNotFoundError):
        load_stored_run(session, run_id="does-not-exist")


def test_load_stored_run_raises_integrity_error_for_a_duplicate_run_id(
    migrated_engine: Engine,
) -> None:
    from aegis.agent.trace_view import TraceIntegrityError, load_stored_run

    run_id = "trace-view-duplicate-run"
    envelope = f'{{"run_id": "{run_id}", "summary": null, "trace": [], "delivery": null}}'
    with Session(migrated_engine) as session, session.begin():
        for dedup in ("dedup-dup-1", "dedup-dup-2"):
            session.execute(
                text(
                    """
                    INSERT INTO incidents
                        (dedup_key, opened_at, window_start, window_end, alert_payload, status,
                         summary_json)
                    VALUES
                        (:dedup, now(), now(), now(), '{}'::jsonb, 'investigating', :envelope)
                    """
                ),
                {"dedup": dedup, "envelope": envelope},
            )

    with Session(migrated_engine) as session:
        with pytest.raises(TraceIntegrityError, match="not unique"):
            load_stored_run(session, run_id=run_id)
