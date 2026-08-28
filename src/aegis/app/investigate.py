"""Synchronous application boundary for incident investigations."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from aegis.agent.summary import IncidentSummary
from aegis.app.run_context import RunContext, TraceEvent
from aegis.config import Settings

if TYPE_CHECKING:
    from aegis.agent.loop import AgentResult


class InvestigationRequest(BaseModel):
    """The model-safe alert details used to start an investigation."""

    model_config = ConfigDict(extra="forbid")

    service: str
    alert_name: str
    fired_at: datetime
    payload: dict[str, Any]
    window_start: datetime
    window_end: datetime

    def brief(self) -> str:
        """Return the only scenario fields allowed to reach the model."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


class AgentTurnLimitExceeded(RuntimeError):
    """Reserved failure raised when the future agent runtime exhausts its turn budget."""


def investigate(
    request: InvestigationRequest,
    run_context: RunContext,
    settings: Settings | None = None,
) -> IncidentSummary:
    """Run an investigation synchronously once the MCP server runtime is available.

    The MCP subprocess must always be terminated and a terminal trace event
    emitted.  Exceptions, including provenance, turn-limit, and transport
    failures, intentionally propagate to the caller after that cleanup.
    """
    failure: BaseException | None = None
    try:
        active_settings = settings or Settings()
        result = asyncio.run(_run_with_transport(request.brief(), run_context, active_settings))
        return result.summary
    except BaseException as exc:
        failure = _unwrap_solo_group(exc)
        raise failure from None
    finally:
        payload: dict[str, str] = {"status": "completed" if failure is None else "failed"}
        if failure is not None:
            payload["error_type"] = type(failure).__name__
        run_context.emit(TraceEvent(kind="terminal", payload=payload))


def _unwrap_solo_group(exc: BaseException) -> BaseException:
    """Return the single meaningful exception inside nested task groups.

    ``stdio_client`` and ``ClientSession`` each open an anyio task group, so a
    ProvenanceError raised inside the transport arrives as
    ``ExceptionGroup(ExceptionGroup(ProvenanceError))``. Without unwrapping, the
    documented contract that these propagate to the caller is false: nothing
    catching ``ProvenanceError`` would ever match. Groups carrying more than one
    exception are left intact, because collapsing them would discard failures.
    """
    current = exc
    while isinstance(current, BaseExceptionGroup) and len(current.exceptions) == 1:
        current = current.exceptions[0]
    return current


async def _run_with_transport(
    brief: str, run_context: RunContext, settings: Settings
) -> AgentResult:
    """Spawn the MCP server, run the agent against its tools, and tear it down.

    Teardown is the ``mcp_tools`` context manager exiting, which happens on
    every path including turn-limit and provenance failures. An earlier version
    held a subprocess handle that was never assigned, so its ``finally`` cleanup
    was a no-op guarding a subprocess that was never spawned -- and the agent
    ran with no tools at all.
    """
    from aegis.agent.transport import mcp_tools  # noqa: PLC0415

    async with mcp_tools(settings) as tools:
        return await run_agent(brief, run_context, settings, tools=tools)


async def run_agent(
    brief: str,
    run_context: RunContext,
    settings: Settings,
    *,
    tools: Iterable[Any] = (),
) -> AgentResult:
    """Defer importing the loop to avoid its public exception-type cycle.

    ``agent.loop`` exports :class:`AgentTurnLimitExceeded` from this module, so
    importing it while this module is initialising would be circular.  Keeping a
    small forwarding seam here also makes the application boundary easy to stub
    without an API key or MCP transport in tests.
    """
    from aegis.agent.loop import run_agent as execute_agent

    return await execute_agent(brief, run_context, settings, tools=tools)


def build_investigation_request(scenario: dict[str, Any]) -> InvestigationRequest:
    """Build a request from a scenario without leaking evaluator-only fields.

    This is intentionally an allowlist rather than a copy-and-filter operation:
    additions to scenario files, including ``expect`` and ``reachability``, do
    not become model context unless this application-owned boundary changes.
    """
    alert = scenario.get("alert")
    if not isinstance(alert, dict):
        raise ValueError("scenario must contain an alert object")

    return InvestigationRequest.model_validate(
        {
            "service": alert.get("service"),
            "alert_name": alert.get("alert_name"),
            "fired_at": alert.get("fired_at"),
            "payload": alert.get("payload"),
            "window_start": alert.get("window_start"),
            "window_end": alert.get("window_end"),
        }
    )


__all__ = [
    "AgentTurnLimitExceeded",
    "InvestigationRequest",
    "build_investigation_request",
    "investigate",
]
