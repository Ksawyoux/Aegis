from __future__ import annotations

from datetime import UTC, datetime
from typing import get_args

import pytest
from pydantic import BaseModel

from aegis.agent.summary import (
    CLAIM_BEARING_FIELDS,
    Claim,
    IncidentSummary,
    ProvenanceError,
    TimelineEntry,
    validate_provenance,
)
from aegis.app.run_context import InMemorySink, RunContext
from aegis.mcp_server.citations import format_log, format_postmortem, format_rollup
from aegis.mcp_server.schemas import Exemplar, Frame, TemplateAnomaly

ROOT_CITE = format_log("a" * 32)
TIMELINE_CITE = format_log("b" * 32)
RULED_OUT_CITE = format_log("c" * 32)
SIMILAR_CITE = format_postmortem("incident", "d" * 64, 0)
FABRICATED_CITE = format_log("e" * 32)


def _summary(**changes: object) -> IncidentSummary:
    values: dict[str, object] = {
        "service": "api",
        "root_cause": Claim(statement="A dependency timed out.", cites=[ROOT_CITE]),
        "confidence": "high",
        "timeline": [
            TimelineEntry(
                at=datetime(2026, 8, 20, tzinfo=UTC),
                what="Errors increased.",
                cites=[TIMELINE_CITE],
            )
        ],
        "ruled_out": [Claim(statement="Database was healthy.", cites=[RULED_OUT_CITE])],
        "similar_incidents": [Claim(statement="Prior timeout.", cites=[SIMILAR_CITE])],
        "recommended_action": "Roll back the dependency change.",
    }
    values.update(changes)
    return IncidentSummary(**values)


def _captured() -> set[str]:
    return {ROOT_CITE, TIMELINE_CITE, RULED_OUT_CITE, SIMILAR_CITE}


def test_fabricated_cite_in_timeline_rejected() -> None:
    summary = _summary(
        timeline=[
            TimelineEntry(
                at=datetime(2026, 8, 20, tzinfo=UTC),
                what="Errors increased.",
                cites=[FABRICATED_CITE],
            )
        ]
    )

    with pytest.raises(ProvenanceError, match="timeline"):
        validate_provenance(summary, _captured())


def test_fabricated_cite_in_ruled_out_rejected() -> None:
    summary = _summary(
        ruled_out=[Claim(statement="Database was healthy.", cites=[FABRICATED_CITE])]
    )

    with pytest.raises(ProvenanceError, match="ruled_out"):
        validate_provenance(summary, _captured())


def test_fabricated_cite_in_similar_incidents_rejected() -> None:
    summary = _summary(
        similar_incidents=[Claim(statement="Prior timeout.", cites=[FABRICATED_CITE])]
    )

    with pytest.raises(ProvenanceError, match="similar_incidents"):
        validate_provenance(summary, _captured())


def test_claim_bearing_fields_tuple_matches_model() -> None:
    def contains_claim_or_timeline(annotation: object) -> bool:
        if annotation in {Claim, TimelineEntry}:
            return True
        return any(contains_claim_or_timeline(argument) for argument in get_args(annotation))

    expected = tuple(
        name
        for name, field_info in IncidentSummary.model_fields.items()
        if contains_claim_or_timeline(field_info.annotation)
    )

    assert CLAIM_BEARING_FIELDS == expected


def test_nested_source_cites_captured() -> None:
    source_cite = format_rollup("api", datetime(2026, 8, 20, tzinfo=UTC), "5xx", "error", "f" * 32)
    baseline_cite = format_rollup(
        "api", datetime(2026, 8, 19, tzinfo=UTC), "5xx", "error", "f" * 32
    )
    exemplar_cite = format_log("1" * 32)

    class Envelope(BaseModel):
        anomaly: TemplateAnomaly

    envelope = Envelope(
        anomaly=TemplateAnomaly(
            template_hash="f" * 32,
            status_class="5xx",
            level="error",
            count=5,
            baseline_count=1,
            delta=4,
            source_cites=[source_cite],
            baseline_cites=[baseline_cite],
            exemplar=Exemplar(
                cite=exemplar_cite,
                sample_message="timeout",
                sample_raw="timeout",
                exc_type=None,
                top_frame=Frame(file="app.py", line=1, func="call"),
                upstream=None,
                duration_ms=None,
                trace_id=None,
            ),
        )
    )
    context = RunContext("run-1", InMemorySink())

    context.capture_tool_result("get_error_telemetry", {}, envelope)

    assert context.captured_cites == {source_cite, baseline_cite, exemplar_cite}
