from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from aegis.agent.summary import Claim, IncidentSummary
from aegis.app.run_context import DatabaseSink, TraceEvent
from aegis.db.models import Incident


def _incident(engine: Engine, dedup_key: str) -> int:
    """Return the incident id, not the instance.

    Returning the ORM object hands the caller a row detached from the closed
    session, so touching any attribute raises DetachedInstanceError.
    """
    with Session(engine) as session, session.begin():
        incident = Incident(
            dedup_key=dedup_key,
            service_id=None,
            opened_at=datetime(2026, 8, 20, tzinfo=UTC),
            window_start=datetime(2026, 8, 20, tzinfo=UTC),
            window_end=datetime(2026, 8, 20, 1, tzinfo=UTC),
            alert_payload={},
            status="open",
        )
        session.add(incident)
        session.flush()
        return incident.id


def _summary() -> IncidentSummary:
    return IncidentSummary(
        service="checkout-api",
        root_cause=Claim(statement="Timeout change caused errors.", cites=["commit:" + "a" * 40]),
        confidence="high",
        timeline=[],
        recommended_action="Restore the timeout.",
    )


def test_database_sink_persists_the_fixed_incident_envelope(migrated_engine: Engine) -> None:
    incident_id = _incident(migrated_engine, "sink-envelope-" + "a" * 16)
    sink = DatabaseSink(migrated_engine, incident_id, "run-123")
    sink.emit(TraceEvent(kind="terminal", payload={"status": "completed"}))

    sink.flush(status="summarized", summary=_summary())

    with Session(migrated_engine) as session:
        stored = session.get(Incident, incident_id)
        assert stored is not None
        assert stored.status == "summarized"
        assert stored.root_cause == "Timeout change caused errors."
        assert stored.summary_json == {
            "run_id": "run-123",
            "summary": _summary().model_dump(mode="json"),
            "trace": [{"kind": "terminal", "payload": {"status": "completed"}}],
            "delivery": None,
        }


def test_database_sink_flush_swallows_database_errors(migrated_engine: Engine) -> None:
    """A sink write must not raise, because it runs inside investigate()'s finally.

    Disposing the caller's engine is not enough to exercise this: dispose only
    closes the pooled connections, so the very next checkout opens a fresh one
    and the write succeeds. The sink is pointed at an unreachable server so the
    failure is real.
    """
    incident_id = _incident(migrated_engine, "sink-swallow-" + "b" * 16)
    unreachable = create_engine(
        "postgresql+psycopg://aegis:aegis@127.0.0.1:1/aegis",
        connect_args={"connect_timeout": 1},
    )
    sink = DatabaseSink(unreachable, incident_id, "run-123")

    sink.flush(status="failed")

    with Session(migrated_engine) as session:
        stored = session.get(Incident, incident_id)
        assert stored is not None
        assert stored.status == "open"
