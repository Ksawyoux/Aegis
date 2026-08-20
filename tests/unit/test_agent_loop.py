from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from aegis.agent.loop import run_agent
from aegis.agent.summary import Claim, IncidentSummary, ProvenanceError
from aegis.app.investigate import AgentTurnLimitExceeded
from aegis.app.run_context import InMemorySink, RunContext
from aegis.config import Settings
from aegis.mcp_server.citations import format_commit, format_log, format_rollup
from aegis.mcp_server.schemas import (
    CommitRef,
    DiffCounts,
    ErrorTelemetry,
    IncidentDiff,
    ResolvedWindow,
    SeriesPoint,
    ServiceChanges,
)

NOW = datetime(2026, 8, 20, 10, tzinfo=UTC)
ROLLUP_CITE = format_rollup("checkout-api", NOW, "5xx", "error", "a" * 32)
COMMIT_CITE = format_commit("b" * 40)
UNSEEN_CITE = format_log("c" * 32)


class RecordingRunContext(RunContext):
    def __init__(self, order: list[str]) -> None:
        super().__init__("test-run", InMemorySink())
        self.order = order
        self.captures: list[tuple[str, dict[str, Any], object]] = []

    def capture_tool_result(self, tool: str, args: dict[str, Any], result: object) -> None:
        self.order.append(f"capture:{tool}")
        self.captures.append((tool, args, result))
        super().capture_tool_result(tool, args, result)  # type: ignore[arg-type]


@dataclass
class FakeRunner:
    turns: list[tuple[dict[str, Any], dict[str, Any] | None]]
    _response: dict[str, Any] | None = field(default=None, init=False)
    generate_calls: int = field(default=0, init=False)

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        for message, response in self.turns:
            self._response = response
            yield message

    async def generate_tool_call_response(self) -> dict[str, Any] | None:
        self.generate_calls += 1
        return self._response


@dataclass
class FakeMessages:
    runner: FakeRunner
    summary: IncidentSummary
    order: list[str]
    runner_kwargs: dict[str, Any] | None = field(default=None, init=False)
    parse_kwargs: dict[str, Any] | None = field(default=None, init=False)

    def tool_runner(self, **kwargs: Any) -> FakeRunner:
        self.runner_kwargs = kwargs
        return self.runner

    async def parse(self, **kwargs: Any) -> SimpleNamespace:
        self.order.append("extract")
        self.parse_kwargs = kwargs
        return SimpleNamespace(parsed_output=self.summary)


@dataclass
class FakeClient:
    beta: SimpleNamespace


def _client(
    turns: list[tuple[dict[str, Any], dict[str, Any] | None]],
    summary: IncidentSummary,
    order: list[str],
) -> tuple[FakeClient, FakeMessages, FakeRunner]:
    runner = FakeRunner(turns)
    messages = FakeMessages(runner, summary, order)
    return FakeClient(beta=SimpleNamespace(messages=messages)), messages, runner


def _window() -> ResolvedWindow:
    return ResolvedWindow(start=NOW, end=NOW + timedelta(minutes=1), snapped=False)


def _telemetry() -> ErrorTelemetry:
    window = _window()
    return ErrorTelemetry(
        effective_window=window,
        baseline_window=ResolvedWindow(start=NOW - timedelta(minutes=1), end=NOW, snapped=False),
        baseline_sparse=False,
        series=[
            SeriesPoint(
                bucket_start=NOW,
                status_class="5xx",
                count=4,
                source_cites=[ROLLUP_CITE],
            )
        ],
        top_templates=[],
        status_breakdown=[],
        sample_trace_ids=[],
    )


def _diff() -> IncidentDiff:
    commit = CommitRef(
        cite=COMMIT_CITE,
        sha="b" * 40,
        message="change",
        authored_at=NOW,
        committed_at=NOW,
        files_changed=[],
    )
    return IncidentDiff(
        window=_window(),
        focus=ServiceChanges(service="checkout-api", commits=[commit], deployments=[]),
        counts=DiffCounts(commits=1, deployments=0, infra_changes=0),
    )


def _summary(cite: str = ROLLUP_CITE) -> IncidentSummary:
    return IncidentSummary(
        service="checkout-api",
        root_cause=Claim(statement="A timeout increased errors.", cites=[cite]),
        confidence="low",
        timeline=[],
        recommended_action="Investigate the timeout.",
    )


def _tool_turn(*blocks: dict[str, Any]) -> dict[str, Any]:
    return {"role": "assistant", "stop_reason": "tool_use", "content": list(blocks)}


def _tool_use(tool_use_id: str, name: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_use_id, "name": name, "input": {"service": name}}


def _tool_result(tool_use_id: str, envelope: object) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": [
            {"type": "text", "text": envelope.model_dump_json()}  # type: ignore[union-attr]
        ],
    }


def _terminal_turn() -> dict[str, Any]:
    return {"role": "assistant", "stop_reason": "end_turn", "content": []}


@pytest.mark.asyncio
async def test_parallel_results_are_matched_by_tool_use_id_and_captured_before_extraction() -> None:
    order: list[str] = []
    tool_turn = _tool_turn(
        _tool_use("diff", "get_incident_diff"),
        _tool_use("telemetry", "get_error_telemetry"),
    )
    response = {
        "role": "user",
        "content": [
            _tool_result("telemetry", _telemetry()),
            _tool_result("diff", _diff()),
        ],
    }
    client, messages, _ = _client(
        [(tool_turn, response), (_terminal_turn(), None)], _summary(), order
    )
    context = RecordingRunContext(order)

    result = await run_agent("investigate", context, Settings(), client=client)

    assert result.turns_used == 2
    assert [capture[0] for capture in context.captures] == [
        "get_incident_diff",
        "get_error_telemetry",
    ]
    assert type(context.captures[0][2]) is IncidentDiff
    assert type(context.captures[1][2]) is ErrorTelemetry
    assert order == ["capture:get_incident_diff", "capture:get_error_telemetry", "extract"]
    assert messages.parse_kwargs is not None


@pytest.mark.asyncio
async def test_error_result_is_traced_but_not_captured_or_cited() -> None:
    order: list[str] = []
    response = {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "telemetry",
                "content": "tool failed",
                "is_error": True,
            }
        ],
    }
    client, _, _ = _client(
        [
            (_tool_turn(_tool_use("telemetry", "get_error_telemetry")), response),
            (_terminal_turn(), None),
        ],
        _summary(UNSEEN_CITE),
        order,
    )
    context = RecordingRunContext(order)

    with pytest.raises(ProvenanceError, match="uncaptured citation"):
        await run_agent("investigate", context, Settings(), client=client)

    assert context.captures == []
    assert context.captured_cites == set()
    assert [event.kind for event in context._events] == ["agent_turn", "error", "agent_turn"]


@pytest.mark.asyncio
async def test_turn_cap_exhaustion_raises_before_extraction() -> None:
    order: list[str] = []
    client, messages, runner = _client(
        [(_tool_turn(_tool_use("telemetry", "get_error_telemetry")), None)], _summary(), order
    )

    with pytest.raises(AgentTurnLimitExceeded):
        await run_agent(
            "investigate",
            RecordingRunContext(order),
            Settings(agent_max_turns=2),
            client=client,
        )

    assert runner.generate_calls == 0
    assert messages.parse_kwargs is None


@pytest.mark.asyncio
async def test_summary_citation_not_seen_in_captured_result_raises() -> None:
    order: list[str] = []
    response = {
        "role": "user",
        "content": [_tool_result("telemetry", _telemetry())],
    }
    client, _, _ = _client(
        [
            (_tool_turn(_tool_use("telemetry", "get_error_telemetry")), response),
            (_terminal_turn(), None),
        ],
        _summary(UNSEEN_CITE),
        order,
    )

    with pytest.raises(ProvenanceError, match="uncaptured citation"):
        await run_agent("investigate", RecordingRunContext(order), Settings(), client=client)
