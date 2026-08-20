"""Synchronous investigation seam reserved for the future MCP agent runtime."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from aegis.agent.summary import IncidentSummary
from aegis.app.run_context import RunContext, TraceEvent


class InvestigationRequest(BaseModel):
    """Opaque request envelope until the v0.1 MCP server and agent loop exist."""

    model_config = ConfigDict(extra="forbid")


class AgentTurnLimitExceeded(RuntimeError):
    """Reserved failure raised when the future agent runtime exhausts its turn budget."""


def investigate(request: InvestigationRequest, run_context: RunContext) -> IncidentSummary:
    """Run an investigation synchronously once the MCP server runtime is available.

    The MCP subprocess must always be terminated and a terminal trace event
    emitted.  Exceptions, including provenance, turn-limit, and transport
    failures, intentionally propagate to the caller after that cleanup.
    """
    del request
    mcp_process: Any | None = None
    failure: BaseException | None = None
    try:
        raise NotImplementedError(
            "aegis.mcp_server.server will provide the MCP subprocess and agent loop"
        )
    except BaseException as exc:
        failure = exc
        raise
    finally:
        _terminate_process(mcp_process)
        payload: dict[str, str] = {"status": "completed" if failure is None else "failed"}
        if failure is not None:
            payload["error_type"] = type(failure).__name__
        run_context.emit(TraceEvent(kind="terminal", payload=payload))


def _terminate_process(process: Any | None) -> None:
    """Terminate a spawned MCP subprocess when the future runtime supplies one."""
    if process is not None:
        process.terminate()


__all__ = ["AgentTurnLimitExceeded", "InvestigationRequest", "investigate"]
