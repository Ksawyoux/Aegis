from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from aegis.agent.loop import _PostmortemHits
from aegis.app.run_context import InMemorySink, RunContext
from aegis.mcp_server.citations import format_log, format_postmortem, format_rollup
from aegis.mcp_server.schemas import PostmortemHit


class NestedEvidence(BaseModel):
    cite: str
    source_cites: list[str]
    baseline_cites: list[str]
    resolution_cite: str | None


class Envelope(BaseModel):
    evidence: list[NestedEvidence]


def _result() -> tuple[Envelope, set[str]]:
    cite = format_log("a" * 32)
    source_cite = format_rollup("api", datetime(2026, 8, 20, tzinfo=UTC), "5xx", "error", "b" * 32)
    baseline_cite = format_rollup(
        "api", datetime(2026, 8, 19, tzinfo=UTC), "5xx", "error", "b" * 32
    )
    resolution_cite = format_postmortem("incident", "c" * 64, 1)
    return (
        Envelope(
            evidence=[
                NestedEvidence(
                    cite=cite,
                    source_cites=[source_cite],
                    baseline_cites=[baseline_cite],
                    resolution_cite=resolution_cite,
                )
            ]
        ),
        {cite, source_cite, baseline_cite, resolution_cite},
    )


def test_nested_envelope_harvests_every_citation_kind() -> None:
    result, expected = _result()
    context = RunContext("caller-run-id", InMemorySink())

    context.capture_tool_result("search", {"z": 1, "a": {"y": 2, "x": 3}}, result)

    assert context.captured_cites == expected


def test_trace_payloads_are_deterministic_across_identical_runs() -> None:
    result, _ = _result()
    first = RunContext("same-run", InMemorySink())
    second = RunContext("same-run", InMemorySink())

    first.capture_tool_result("search", {"z": 1, "a": {"y": 2, "x": 3}}, result)
    second.capture_tool_result("search", {"z": 1, "a": {"y": 2, "x": 3}}, result)

    assert first.to_json() == second.to_json()
    assert list(first.to_json()["trace"][0]["payload"]["args"]) == ["a", "z"]


def test_run_id_is_caller_supplied() -> None:
    context = RunContext("external-correlation-id", InMemorySink())

    assert context.run_id == "external-correlation-id"
    assert context.to_json()["run_id"] == "external-correlation-id"


def test_list_root_postmortem_result_harvests_match_and_resolution_citations() -> None:
    match = format_postmortem("pool-incident", "a" * 64, 0)
    resolution = format_postmortem("pool-incident", "a" * 64, 1)
    result = _PostmortemHits(
        root=[
            PostmortemHit(
                cite=match,
                resolution_cite=resolution,
                slug="pool-incident",
                title="Pool incident",
                occurred_at=None,
                snippet="pool exhausted",
                resolution_md="increase pool",
                similarity=1.0,
            )
        ]
    )
    context = RunContext("run", InMemorySink())

    context.capture_tool_result("search_similar_postmortems", {}, result)

    assert context.captured_cites == {match, resolution}
