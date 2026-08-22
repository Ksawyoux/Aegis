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
class FakeTool:
    """One adapted MCP tool double with canned invocation results."""

    name: str
    result_text: str = ""
    is_error: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)

    def as_openai_tool(self) -> dict[str, Any]:
        return {"type": "function", "name": self.name, "parameters": {"type": "object"}}

    async def invoke(self, arguments: dict[str, Any]) -> tuple[str, bool]:
        self.calls.append(arguments)
        return self.result_text, self.is_error


@dataclass
class FakeResponses:
    turns: list[dict[str, Any]]
    summary: IncidentSummary
    order: list[str]
    create_kwargs: list[dict[str, Any]] = field(default_factory=list)
    parse_kwargs: dict[str, Any] | None = field(default=None, init=False)

    async def create(self, **kwargs: Any) -> dict[str, Any]:
        self.create_kwargs.append(kwargs)
        return self.turns.pop(0)

    async def parse(self, **kwargs: Any) -> SimpleNamespace:
        self.order.append("extract")
        self.parse_kwargs = kwargs
        return SimpleNamespace(output_parsed=self.summary)


@dataclass
class FakeClient:
    responses: FakeResponses


def _client(
    turns: list[dict[str, Any]],
    summary: IncidentSummary,
    order: list[str],
) -> FakeClient:
    return FakeClient(responses=FakeResponses(turns, summary, order))


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


def _call(call_id: str, name: str) -> dict[str, Any]:
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": '{"service": "' + name + '"}',
    }


def _turn(*items: dict[str, Any]) -> dict[str, Any]:
    return {"output": list(items)}


def _terminal_turn() -> dict[str, Any]:
    return {"output": [{"type": "message", "role": "assistant", "content": []}]}


def _default_tools() -> list[FakeTool]:
    return [
        FakeTool("get_incident_diff", result_text=_diff().model_dump_json()),
        FakeTool("get_error_telemetry", result_text=_telemetry().model_dump_json()),
    ]


@pytest.mark.asyncio
async def test_parallel_calls_are_matched_by_call_id_and_captured_before_extraction() -> None:
    order: list[str] = []
    tools = _default_tools()
    client = _client(
        [
            _turn(_call("diff", "get_incident_diff"), _call("telemetry", "get_error_telemetry")),
            _terminal_turn(),
        ],
        _summary(),
        order,
    )
    context = RecordingRunContext(order)

    result = await run_agent("investigate", context, Settings(), client=client, tools=tools)

    assert result.turns_used == 2
    assert [capture[0] for capture in context.captures] == [
        "get_incident_diff",
        "get_error_telemetry",
    ]
    assert type(context.captures[0][2]) is IncidentDiff
    assert type(context.captures[1][2]) is ErrorTelemetry
    assert order == ["capture:get_incident_diff", "capture:get_error_telemetry", "extract"]
    assert client.responses.parse_kwargs is not None


@pytest.mark.asyncio
async def test_error_result_is_traced_but_not_captured_or_cited() -> None:
    order: list[str] = []
    failing = FakeTool("get_error_telemetry", result_text="tool failed", is_error=True)
    client = _client(
        [
            _turn(_call("telemetry", "get_error_telemetry")),
            _terminal_turn(),
        ],
        _summary(UNSEEN_CITE),
        order,
    )
    context = RecordingRunContext(order)

    with pytest.raises(ProvenanceError, match="uncaptured citation"):
        await run_agent("investigate", context, Settings(), client=client, tools=[failing])

    assert context.captures == []
    assert context.captured_cites == set()
    assert [event.kind for event in context._events] == ["agent_turn", "error", "agent_turn"]


@pytest.mark.asyncio
async def test_turn_cap_exhaustion_raises_before_execution_and_extraction() -> None:
    order: list[str] = []
    tool = FakeTool("get_error_telemetry", result_text=_telemetry().model_dump_json())
    client = _client([_turn(_call("telemetry", "get_error_telemetry"))], _summary(), order)

    with pytest.raises(AgentTurnLimitExceeded):
        await run_agent(
            "investigate",
            RecordingRunContext(order),
            Settings(agent_max_turns=2),
            client=client,
            tools=[tool],
        )

    assert tool.calls == []
    assert len(client.responses.create_kwargs) == 1
    assert client.responses.parse_kwargs is None


@pytest.mark.asyncio
async def test_summary_citation_not_seen_in_captured_result_raises() -> None:
    order: list[str] = []
    tools = _default_tools()
    client = _client(
        [
            _turn(_call("telemetry", "get_error_telemetry")),
            _terminal_turn(),
        ],
        _summary(UNSEEN_CITE),
        order,
    )

    with pytest.raises(ProvenanceError, match="uncaptured citation"):
        await run_agent(
            "investigate", RecordingRunContext(order), Settings(), client=client, tools=tools
        )


@pytest.mark.asyncio
async def test_unknown_tool_degrades_to_an_error_result_the_model_can_recover_from() -> None:
    order: list[str] = []
    tools = _default_tools()
    client = _client(
        [
            _turn(_call("mystery", "not_a_registered_tool")),
            _terminal_turn(),
        ],
        _summary(UNSEEN_CITE),
        order,
    )
    context = RecordingRunContext(order)

    with pytest.raises(ProvenanceError, match="uncaptured citation"):
        await run_agent("investigate", context, Settings(), client=client, tools=tools)

    assert [event.kind for event in context._events] == ["agent_turn", "error", "agent_turn"]
    outputs = [
        item
        for item in client.responses.create_kwargs[-1]["input"]
        if item.get("type") == "function_call_output"
    ]
    assert outputs == [
        {
            "type": "function_call_output",
            "call_id": "mystery",
            "output": '{"error": "unknown tool \'not_a_registered_tool\'"}',
        }
    ]
