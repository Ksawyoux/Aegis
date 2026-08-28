from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine

from aegis.agent.summary import Claim, IncidentSummary
from aegis.app.investigate import InvestigationRequest
from aegis.app.records import DeliveryOutcome
from aegis.config import Settings


def _request() -> InvestigationRequest:
    return InvestigationRequest(
        service="checkout-api",
        alert_name="HighErrorRate",
        fired_at=datetime(2026, 8, 20, 10, tzinfo=UTC),
        payload={},
        window_start=datetime(2026, 8, 20, 9, tzinfo=UTC),
        window_end=datetime(2026, 8, 20, 10, tzinfo=UTC),
    )


def _summary() -> IncidentSummary:
    return IncidentSummary(
        service="checkout-api",
        root_cause=Claim(statement="Timeout changed.", cites=["commit:" + "a" * 40]),
        confidence="high",
        timeline=[],
        recommended_action="Restore the timeout.",
    )


def test_runner_persists_investigating_summary_and_delivery_in_order(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict[str, object]] = []

    class RecordingSink:
        def __init__(self, *_args: object) -> None:
            return None

        def emit(self, _event: object) -> None:
            return None

        def flush(self, **kwargs: object) -> None:
            calls.append(kwargs)

    monkeypatch.setattr("aegis.app.runner.DatabaseSink", RecordingSink)
    monkeypatch.setattr("aegis.app.runner.investigate", lambda *_args: _summary())

    from aegis.app.runner import run_incident

    run_incident(
        1,
        _request(),
        Settings(),
        create_engine("sqlite://"),
        deliver=lambda *_args: DeliveryOutcome(attempted=True, ok=True, status_code=200),
    )

    assert [call["status"] for call in calls] == ["investigating", "summarized", "summarized"]
    assert calls[1]["summary"] == _summary()
    assert calls[2]["delivery"] == DeliveryOutcome(attempted=True, ok=True, status_code=200)


def test_runner_records_failed_investigation_without_reraising(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict[str, object]] = []

    class RecordingSink:
        def __init__(self, *_args: object) -> None:
            return None

        def emit(self, _event: object) -> None:
            return None

        def flush(self, **kwargs: object) -> None:
            calls.append(kwargs)

    def fail(*_args: object) -> IncidentSummary:
        raise RuntimeError("agent failed")

    monkeypatch.setattr("aegis.app.runner.DatabaseSink", RecordingSink)
    monkeypatch.setattr("aegis.app.runner.investigate", fail)

    from aegis.app.runner import run_incident

    run_incident(1, _request(), Settings(), create_engine("sqlite://"))

    assert [call["status"] for call in calls] == ["investigating", "failed"]
