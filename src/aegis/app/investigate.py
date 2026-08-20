"""Synchronous application boundary for incident investigations."""

from __future__ import annotations

import asyncio
import json
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


def investigate(request: InvestigationRequest, run_context: RunContext) -> IncidentSummary:
    """Run an investigation synchronously once the MCP server runtime is available.

    The MCP subprocess must always be terminated and a terminal trace event
    emitted.  Exceptions, including provenance, turn-limit, and transport
    failures, intentionally propagate to the caller after that cleanup.
    """
    mcp_process: Any | None = None
    failure: BaseException | None = None
    try:
        result = asyncio.run(run_agent(request.brief(), run_context, Settings()))
        return result.summary
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            _terminate_process(mcp_process)
        finally:
            payload: dict[str, str] = {"status": "completed" if failure is None else "failed"}
            if failure is not None:
                payload["error_type"] = type(failure).__name__
            run_context.emit(TraceEvent(kind="terminal", payload=payload))


def _terminate_process(process: Any | None) -> None:
    """Terminate a spawned MCP subprocess when the future runtime supplies one."""
    if process is not None:
        process.terminate()


async def run_agent(
    brief: str, run_context: RunContext, settings: Settings
) -> AgentResult:
    """Defer importing the loop to avoid its public exception-type cycle.

    ``agent.loop`` exports :class:`AgentTurnLimitExceeded` from this module, so
    importing it while this module is initialising would be circular.  Keeping a
    small forwarding seam here also makes the application boundary easy to stub
    without an API key or MCP transport in tests.
    """
    from aegis.agent.loop import run_agent as execute_agent

    return await execute_agent(brief, run_context, settings)


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
