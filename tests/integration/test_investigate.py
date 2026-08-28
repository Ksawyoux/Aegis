from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aegis.agent.loop import AgentResult
from aegis.agent.summary import Claim, IncidentSummary, ProvenanceError
from aegis.app.investigate import (
    AgentTurnLimitExceeded,
    InvestigationRequest,
    investigate,
)
from aegis.app.run_context import InMemorySink, RunContext
from aegis.config import Settings


def _request() -> InvestigationRequest:
    return InvestigationRequest(
        service="checkout-api",
        alert_name="HighErrorRate",
        fired_at=datetime(2026, 8, 20, 10, tzinfo=UTC),
        payload={"severity": "critical"},
        window_start=datetime(2026, 8, 20, 9, tzinfo=UTC),
        window_end=datetime(2026, 8, 20, 10, tzinfo=UTC),
    )


def _summary() -> IncidentSummary:
    return IncidentSummary(
        service="checkout-api",
        root_cause=Claim(
            statement="The timeout change caused errors.", cites=["commit:" + "a" * 40]
        ),
        confidence="high",
        timeline=[],
        recommended_action="Restore the timeout.",
    )


def _context() -> tuple[RunContext, InMemorySink]:
    sink = InMemorySink()
    return RunContext("run-1", sink), sink


def test_success_returns_summary_and_emits_terminal_event(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_agent(*_args: object, **_kwargs: object) -> AgentResult:
        return AgentResult(summary=_summary(), turns_used=2)

    monkeypatch.setattr("aegis.app.investigate._run_with_transport", fake_run_agent)
    context, sink = _context()

    assert investigate(_request(), context) == _summary()
    assert sink.events[-1].kind == "terminal"
    assert sink.events[-1].payload == {"status": "completed"}


def test_investigate_passes_supplied_settings_to_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[object] = []

    async def fake_run_agent(*args: object, **_kwargs: object) -> AgentResult:
        captured.append(args[2])
        return AgentResult(summary=_summary(), turns_used=2)

    monkeypatch.setattr("aegis.app.investigate._run_with_transport", fake_run_agent)
    settings = Settings(
        database_url="postgresql+psycopg://example.invalid/aegis"
    )

    assert investigate(_request(), _context()[0], settings) == _summary()
    assert captured == [settings]


@pytest.mark.parametrize(
    "failure",
    [
        ProvenanceError("uncaptured citation"),
        AgentTurnLimitExceeded(3),
    ],
)
def test_failure_propagates_after_terminal_trace_event(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    async def fake_run_agent(*_args: object, **_kwargs: object) -> AgentResult:
        raise failure

    monkeypatch.setattr("aegis.app.investigate._run_with_transport", fake_run_agent)
    context, sink = _context()

    with pytest.raises(type(failure)):
        investigate(_request(), context)

    assert sink.events[-1].kind == "terminal"
    assert sink.events[-1].payload == {"status": "failed", "error_type": type(failure).__name__}
