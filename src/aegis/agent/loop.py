"""The evidence-capturing state machine for an incident investigation."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from openai import AsyncOpenAI
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
"""The only success-shaped tool responses which may become captured evidence."""

_TOOL_MAX_TOKENS = 4_096
_SUMMARY_MAX_TOKENS = 2_048
_SUMMARY_REQUEST = "Return the structured incident summary now, with citations for every claim."


@dataclass(frozen=True)
class AgentResult:
    """The validated summary and number of API turns consumed before it."""

    summary: IncidentSummary
    turns_used: int


class _Tool(Protocol):
    """What the loop needs from a transport-adapted tool."""

    name: str

    def as_openai_tool(self) -> dict[str, Any]: ...

    def invoke(self, arguments: dict[str, Any]) -> Awaitable[tuple[str, bool]]: ...


class _Responses(Protocol):
    async def create(self, **kwargs: Any) -> Any: ...

    async def parse(self, **kwargs: Any) -> Any: ...


class _Client(Protocol):
    responses: _Responses


async def run_agent(
    brief: str,
    run_context: RunContext,
    settings: Settings,
    *,
    client: _Client | None = None,
    tools: Iterable[Any] = (),
) -> AgentResult:
    """Run tool turns, capture validated envelopes, then extract one summary.

    Each turn is one ``responses.create`` call. A turn whose output carries
    function calls is executed against the adapted MCP tools and answered with
    ``function_call_output`` items before the next request; a terminal turn --
    one without function calls -- hands the accumulated history to structured
    output for extraction.

    Production callers may omit ``client``; transport tools must be supplied
    by that later package.
    """
    active_client = client if client is not None else cast(_Client, _default_client(settings))
    tool_list: list[_Tool] = list(tools)
    lookup = {tool.name: tool for tool in tool_list}
    history: list[dict[str, Any]] = [{"role": "user", "content": brief}]
    budget = settings.agent_max_turns - 1
    turns_used = 0

    while True:
        response = await active_client.responses.create(
            model=settings.openai_model,
            input=history,
            tools=[tool.as_openai_tool() for tool in tool_list],
            reasoning={"effort": settings.agent_effort},
            max_output_tokens=_TOOL_MAX_TOKENS,
            instructions=SYSTEM_PROMPT,
        )
        calls = [item for item in _output(response) if _field(item, "type") == "function_call"]
        turns_used += 1
        run_context.emit(
            TraceEvent(
                kind="agent_turn",
                payload={
                    "turn": turns_used,
                    "function_calls": [_field(call, "name") for call in calls],
                },
            )
        )
        if not calls:
            break
        if turns_used >= budget:
            raise AgentTurnLimitExceeded(turns_used)

        history.extend(
            {
                "type": "function_call",
                "call_id": _field(call, "call_id"),
                "name": _field(call, "name"),
                "arguments": _field(call, "arguments"),
            }
            for call in calls
        )
        history.extend(await _execute_calls(calls, lookup, run_context))

    parsed = await active_client.responses.parse(
        model=settings.openai_model,
        input=[*history, {"role": "user", "content": _SUMMARY_REQUEST}],
        text_format=IncidentSummary,
        reasoning={"effort": settings.agent_effort},
        max_output_tokens=_SUMMARY_MAX_TOKENS,
        instructions=SYSTEM_PROMPT,
    )
    summary = cast(IncidentSummary, parsed.output_parsed)
    validate_provenance(summary, run_context.captured_cites)
    return AgentResult(summary=summary, turns_used=turns_used)


def _default_client(settings: Settings) -> AsyncOpenAI:
    """Build the production client from Settings, not the ambient environment.

    ``AEGIS_OPENAI_BASE_URL`` is how this repository pins a provider endpoint
    for embeddings; the agent must follow the same setting, or a rehearsal
    against a local provider would silently call the real API.
    """
    return AsyncOpenAI(
        base_url=settings.openai_base_url,
        api_key=(
            settings.openai_api_key.get_secret_value()
            if settings.openai_api_key is not None
            else None
        ),
    )


async def _execute_calls(
    calls: list[Any], lookup: dict[str, Any], run_context: RunContext
) -> list[dict[str, str]]:
    """Execute every function call in one turn and build its output items."""
    outputs: list[dict[str, str]] = []
    for call in calls:
        name = _field(call, "name")
        call_id = _field(call, "call_id")
        args = _parse_arguments(call)
        text, is_error = await _invoke(lookup.get(name), name, args)

        if is_error:
            run_context.emit(
                TraceEvent(
                    kind="error",
                    payload={"tool": name, "args": args, "call_id": call_id},
                )
            )
        else:
            # Validate before capture so an off-spec success payload is stored
            # as evidence only when it actually matches its declared envelope.
            envelope_args = args if isinstance(args, dict) else {}
            envelope = ENVELOPE_BY_TOOL[name].model_validate_json(text)
            run_context.capture_tool_result(name, envelope_args, envelope)
        outputs.append({"type": "function_call_output", "call_id": call_id, "output": text})
    return outputs


async def _invoke(tool: _Tool | None, name: str, args: object) -> tuple[str, bool]:
    """Dispatch one call, degrading an unknown tool or bad arguments to an error result."""
    if tool is None:
        return json.dumps({"error": f"unknown tool {name!r}"}), True
    if not isinstance(args, dict):
        error = f"arguments must be an object, got {type(args).__name__}"
        return json.dumps({"error": error}), True
    return await tool.invoke(args)


def _parse_arguments(call: Any) -> object:
    raw = _field(call, "arguments")
    if not isinstance(raw, str):
        raise RuntimeError("function call arguments must be a JSON string")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"function call arguments are not valid JSON: {raw!r}") from error


def _output(response: Any) -> list[Any]:
    output = _field(response, "output")
    if not isinstance(output, list):
        raise RuntimeError("response output must be a list of items")
    return output


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


__all__ = ["AgentResult", "ENVELOPE_BY_TOOL", "run_agent"]
