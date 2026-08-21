"""Unit tests for stored-run rendering and integrity validation.

``StoredRun.record`` is typed as Part 3's ``IncidentRecord``, but a plain
``dataclasses.dataclass`` performs no runtime type enforcement, so these tests
build a minimal duck-typed stand-in exposing exactly the four attributes
(``run_id``, ``summary``, ``trace``, ``delivery``) that ``render_trace`` and
``validate_trace_integrity`` actually read. This lets rendering and integrity
logic be fully exercised without Part 3's ``aegis.app.records`` module, which
is not present in this tree. ``load_stored_run`` itself imports that module
directly and cannot be exercised the same way; see
``test_trace_view_db.py`` for its explicit skip.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from aegis.agent.summary import Claim, IncidentSummary, TimelineEntry
from aegis.agent.trace_view import (
    StoredRun,
    TraceIntegrityError,
    render_trace,
    validate_trace_integrity,
)


@dataclass
class _FakeRecord:
    """Stands in for Part 3's ``IncidentRecord`` -- same four fields, no validation."""

    run_id: str
    summary: IncidentSummary | None
    trace: list[dict[str, Any]]
    delivery: dict[str, Any] | None = None


def _tool_result_event(tool: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "tool_result", "payload": {"tool": tool, "args": {}, "result": result}}


def _agent_turn_event(turn: int, stop_reason: str) -> dict[str, Any]:
    return {"kind": "agent_turn", "payload": {"turn": turn, "stop_reason": stop_reason}}


def _terminal_event(status: str, **extra: Any) -> dict[str, Any]:
    return {"kind": "terminal", "payload": {"status": status, **extra}}


def _summary(cite: str) -> IncidentSummary:
    return IncidentSummary(
        service="checkout-api",
        root_cause=Claim(statement="timeout misconfigured", cites=[cite]),
        confidence="high",
        timeline=[
            TimelineEntry(
                at=datetime(2026, 8, 19, 14, 0, tzinfo=UTC), what="deploy landed", cites=[cite]
            )
        ],
        ruled_out=[],
        similar_incidents=[],
        recommended_action="revert the timeout change",
    )


class TestRenderTrace:
    def test_numbers_events_by_list_position_and_shows_incident_header(self) -> None:
        cite = "commit:" + "a" * 40
        trace = [
            _agent_turn_event(1, "tool_use"),
            _tool_result_event("get_incident_diff", {"cite": cite}),
            _agent_turn_event(2, "end_turn"),
            _terminal_event("completed"),
        ]
        run = StoredRun(
            incident_id=41,
            dedup_key="demo-eval:checkout-5xx-spike",
            incident_status="summarized",
            record=_FakeRecord(run_id="abc123", summary=_summary(cite), trace=trace),  # type: ignore[arg-type]
        )

        rendered = render_trace(run)

        assert "Run abc123" in rendered
        assert "Incident 41 · demo-eval:checkout-5xx-spike · summarized" in rendered
        assert "Delivery: not attempted" in rendered
        assert "000 agent_turn turn=1 stop_reason=tool_use" in rendered
        assert "001 tool_result get_incident_diff cites=1" in rendered
        assert "002 agent_turn turn=2 stop_reason=end_turn" in rendered
        assert "003 terminal status=completed" in rendered
        assert f"{cite}" in rendered
        assert "captured at event 001" in rendered

    def test_missing_citation_is_rendered_inline_rather_than_raising(self) -> None:
        cite = "commit:" + "a" * 40
        trace = [_terminal_event("completed")]
        run = StoredRun(
            incident_id=1,
            dedup_key="k",
            incident_status="summarized",
            record=_FakeRecord(run_id="r1", summary=_summary(cite), trace=trace),  # type: ignore[arg-type]
        )

        rendered = render_trace(run)

        assert "MISSING FROM TRACE" in rendered

    def test_unknown_event_kind_is_rendered_generically_not_dropped(self) -> None:
        trace = [{"kind": "future_kind", "payload": {"a": 1, "b": "two"}}]
        run = StoredRun(
            incident_id=1,
            dedup_key="k",
            incident_status="investigating",
            record=_FakeRecord(run_id="r1", summary=None, trace=trace),  # type: ignore[arg-type]
        )

        rendered = render_trace(run)

        assert "future_kind" in rendered
        assert "a=1" in rendered
        assert "b=two" in rendered

    def test_full_flag_includes_payloads(self) -> None:
        trace = [_agent_turn_event(1, "end_turn")]
        run = StoredRun(
            incident_id=1,
            dedup_key="k",
            incident_status="failed",
            record=_FakeRecord(run_id="r1", summary=None, trace=trace),  # type: ignore[arg-type]
        )

        compact = render_trace(run, include_payloads=False)
        full = render_trace(run, include_payloads=True)

        assert "payload:" not in compact
        assert "payload:" in full


class TestValidateTraceIntegrity:
    def test_summarized_with_completed_terminal_and_captured_citations_passes(self) -> None:
        cite = "commit:" + "a" * 40
        trace = [
            _tool_result_event("get_incident_diff", {"cite": cite}),
            _terminal_event("completed"),
        ]
        run = StoredRun(
            incident_id=1,
            dedup_key="k",
            incident_status="summarized",
            record=_FakeRecord(run_id="r1", summary=_summary(cite), trace=trace),  # type: ignore[arg-type]
        )

        validate_trace_integrity(run)  # must not raise

    def test_more_than_one_terminal_event_fails(self) -> None:
        trace = [_terminal_event("completed"), _terminal_event("completed")]
        run = StoredRun(
            incident_id=1,
            dedup_key="k",
            incident_status="summarized",
            record=_FakeRecord(run_id="r1", summary=None, trace=trace),  # type: ignore[arg-type]
        )

        with pytest.raises(TraceIntegrityError, match="more than one terminal event"):
            validate_trace_integrity(run)

    def test_summarized_status_requires_a_stored_summary(self) -> None:
        trace = [_terminal_event("completed")]
        run = StoredRun(
            incident_id=1,
            dedup_key="k",
            incident_status="summarized",
            record=_FakeRecord(run_id="r1", summary=None, trace=trace),  # type: ignore[arg-type]
        )

        with pytest.raises(TraceIntegrityError, match="no summary is stored"):
            validate_trace_integrity(run)

    def test_failed_status_rejects_a_stored_summary(self) -> None:
        cite = "commit:" + "a" * 40
        trace = [_tool_result_event("t", {"cite": cite}), _terminal_event("failed")]
        run = StoredRun(
            incident_id=1,
            dedup_key="k",
            incident_status="failed",
            record=_FakeRecord(run_id="r1", summary=_summary(cite), trace=trace),  # type: ignore[arg-type]
        )

        with pytest.raises(TraceIntegrityError, match="a summary is stored"):
            validate_trace_integrity(run)

    def test_a_poisoned_summary_with_an_uncaptured_citation_fails_integrity(self) -> None:
        real_cite = "commit:" + "a" * 40
        fabricated_cite = "commit:" + "f" * 40
        trace = [_tool_result_event("t", {"cite": real_cite}), _terminal_event("completed")]
        run = StoredRun(
            incident_id=1,
            dedup_key="k",
            incident_status="summarized",
            record=_FakeRecord(
                run_id="r1", summary=_summary(fabricated_cite), trace=trace
            ),  # type: ignore[arg-type]
        )

        with pytest.raises(TraceIntegrityError, match="not found in any captured tool result"):
            validate_trace_integrity(run)

        # The render still succeeds and shows the discrepancy inline.
        assert "MISSING FROM TRACE" in render_trace(run)
