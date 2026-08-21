"""The evidence-capturing state machine for an incident investigation."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from anthropic import AsyncAnthropic
from pydantic import BaseModel, RootModel

from aegis.agent.prompt import SYSTEM_PROMPT
from aegis.agent.summary import IncidentSummary, validate_provenance
from aegis.app.investigate import AgentTurnLimitExceeded
from aegis.app.run_context import RunContext, TraceEvent
from aegis.config import Settings
from aegis.mcp_server.schemas import ErrorTelemetry, IncidentDiff, PostmortemHit


class _PostmortemHits(RootModel[list[PostmortemHit]]):
    pass


ENVELOPE_BY_TOOL: dict[str, type[BaseModel]] = {
    "get_incident_diff": IncidentDiff,
    "get_error_telemetry": ErrorTelemetry,
    "search_similar_postmortems": _PostmortemHits,
}
"""The only success-shaped MCP responses which may become captured evidence."""

_TOOL_MAX_TOKENS = 4_096
_SUMMARY_MAX_TOKENS = 2_048
_SUMMARY_REQUEST = "Return the structured incident summary now, with citations for every claim."


@dataclass(frozen=True)
class AgentResult:
    """The validated summary and number of Messages API turns consumed before it."""

    summary: IncidentSummary
    turns_used: int


class _Runner(Protocol):
    def __aiter__(self) -> AsyncIterator[Any]: ...

    async def generate_tool_call_response(self) -> Mapping[str, Any] | None: ...


class _Messages(Protocol):
    def tool_runner(self, **kwargs: Any) -> _Runner: ...

    async def parse(self, **kwargs: Any) -> Any: ...


class _Beta(Protocol):
    messages: _Messages


class _Client(Protocol):
    beta: _Beta


async def run_agent(
    brief: str,
    run_context: RunContext,
    settings: Settings,
    *,
    client: _Client | None = None,
    tools: Iterable[Any] = (),
) -> AgentResult:
    """Run tool turns, capture validated envelopes, then extract one summary.

    ``client`` and ``tools`` are dependency-injection seams for the MCP package
    that owns transport wiring and for deterministic tests. Production callers
    may omit ``client``; transport tools must be supplied by that later package.
    """
    active_client = client if client is not None else cast(_Client, AsyncAnthropic())
    history: list[dict[str, Any]] = [{"role": "user", "content": brief}]
    budget = settings.agent_max_turns - 1
    turns_used = 0
    runner = active_client.beta.messages.tool_runner(
        model=settings.anthropic_model,
        max_tokens=_TOOL_MAX_TOKENS,
        messages=history,
        tools=tools,
        system=SYSTEM_PROMPT,
        output_config={"effort": settings.agent_effort},
    )

    async for message in runner:
        turns_used += 1
        run_context.emit(
            TraceEvent(
                kind="agent_turn",
                payload={"turn": turns_used, "stop_reason": _field(message, "stop_reason")},
            )
        )
        if _field(message, "stop_reason") != "tool_use":
            break
        if turns_used >= budget:
            raise AgentTurnLimitExceeded(turns_used)

        response = await runner.generate_tool_call_response()
        if response is None:
            raise RuntimeError("tool runner yielded tool_use without a tool-result response")
        _capture_tool_results(message, response, run_context)
        history.append(_assistant_message(message))
        history.append(dict(response))

    parsed = await active_client.beta.messages.parse(
        model=settings.anthropic_model,
        max_tokens=_SUMMARY_MAX_TOKENS,
        messages=[*history, {"role": "user", "content": _SUMMARY_REQUEST}],
        system=SYSTEM_PROMPT,
        output_format=IncidentSummary,
        output_config={"effort": settings.agent_effort},
    )
    summary = cast(IncidentSummary, parsed.parsed_output)
    validate_provenance(summary, run_context.captured_cites)
    return AgentResult(summary=summary, turns_used=turns_used)


def _capture_tool_results(
    message: Any, response: Mapping[str, Any], run_context: RunContext
) -> None:
    """Validate and capture successful results, joining blocks by ``tool_use_id``."""
    results = {
        _field(block, "tool_use_id"): block
        for block in _content(response)
        if _field(block, "type") == "tool_result"
    }
    for block in _content(message):
        if _field(block, "type") != "tool_use":
            continue
        tool_use_id = _field(block, "id")
        result = results.get(tool_use_id)
        if result is None:
            raise RuntimeError(f"missing tool result for tool use {tool_use_id!r}")

        name = _field(block, "name")
        args = _field(block, "input")
        if not isinstance(name, str) or not isinstance(args, dict):
            raise RuntimeError("tool_use block has an invalid name or input")
        if _field(result, "is_error", False):
            run_context.emit(
                TraceEvent(
                    kind="error",
                    payload={"tool": name, "args": args, "tool_use_id": tool_use_id},
                )
            )
            continue

        envelope_type = ENVELOPE_BY_TOOL[name]
        envelope = envelope_type.model_validate_json(_result_text(result))
        run_context.capture_tool_result(name, args, envelope)


def _assistant_message(message: Any) -> dict[str, Any]:
    """Copy the yielded assistant message into extraction history."""
    return {"role": _field(message, "role"), "content": _content(message)}


def _content(value: Any) -> list[Any]:
    content = _field(value, "content")
    if not isinstance(content, list):
        raise RuntimeError("message content must be a list of blocks")
    return content


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _result_text(result: Any) -> str:
    """Extract JSON text from Anthropic's string or text-block tool result form."""
    content = _field(result, "content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise RuntimeError("tool result content must be text")
    texts = [_field(block, "text") for block in content if _field(block, "type") == "text"]
    if not texts or any(not isinstance(text, str) for text in texts):
        raise RuntimeError("tool result did not contain text blocks")
    return "".join(cast(list[str], texts))


__all__ = ["AgentResult", "ENVELOPE_BY_TOOL", "run_agent"]
