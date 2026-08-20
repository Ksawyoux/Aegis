"""The v0.1 Aegis MCP server, served exclusively over standard I/O."""

from __future__ import annotations

import re
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from sqlalchemy.orm import Session

from aegis.config import Settings
from aegis.db.session import get_session
from aegis.mcp_server.queries import QueryError, get_error_telemetry, get_incident_diff
from aegis.mcp_server.schemas import ErrorTelemetry, IncidentDiff

_INVALID_REQUEST_MESSAGE = "Invalid query request."
_INVALID_TIMESTAMP_MESSAGE = "Timestamps must be RFC-3339 strings with an explicit timezone."
_RFC3339_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def create_server(settings: Settings | None = None) -> FastMCP:
    """Create the v0.1 server with its two aggregate-only tools."""
    active_settings = settings or Settings()
    mcp = FastMCP("aegis-context")

    @mcp.tool(name="get_incident_diff")
    def get_incident_diff_tool(
        service: str,
        window_start: str,
        window_end: str,
        lookback_minutes: int = 60,
        include_other_services: bool = True,
    ) -> IncidentDiff:
        """Return deploy, commit, and infrastructure changes for one service.

        The incident window is half-open: ``[window_start, window_end)``.  The
        returned ``window`` is the range actually measured after minute snapping
        and adding ``lookback_minutes``.  Evidence citations are carried by
        ``cite`` fields on rows, including nested deployment commits.
        """
        start = _parse_timestamp(window_start)
        end = _parse_timestamp(window_end)
        with _session() as session:
            try:
                return get_incident_diff(
                    session,
                    service=service,
                    window_start=start,
                    window_end=end,
                    lookback_minutes=lookback_minutes,
                    include_other_services=include_other_services,
                )
            except (QueryError, ValueError) as error:
                raise ToolError(_INVALID_REQUEST_MESSAGE) from error

    @mcp.tool(name="get_error_telemetry")
    def get_error_telemetry_tool(
        service: str,
        window_start: str,
        window_end: str,
        top_n: int = 10,
    ) -> ErrorTelemetry:
        """Return aggregate error telemetry and baseline comparisons for one service.

        The incident window is half-open: ``[window_start, window_end)``.  The
        returned ``effective_window`` and ``baseline_window`` describe the ranges
        actually measured after minute snapping.  Evidence citations are in
        ``source_cites`` / ``baseline_cites`` and each template exemplar's
        ``cite`` field.
        """
        start = _parse_timestamp(window_start)
        end = _parse_timestamp(window_end)
        with _session() as session:
            try:
                return get_error_telemetry(
                    session,
                    service=service,
                    window_start=start,
                    window_end=end,
                    top_n=top_n,
                    baseline_sparse_threshold=active_settings.baseline_sparse_threshold,
                )
            except (QueryError, ValueError) as error:
                raise ToolError(_INVALID_REQUEST_MESSAGE) from error

    return mcp


@contextmanager
def _session() -> Generator[Session, None, None]:
    """Open one database session for one tool call and always close it."""
    session_generator = get_session()
    session = next(session_generator)
    try:
        yield session
    finally:
        session_generator.close()


def _parse_timestamp(value: str) -> datetime:
    """Parse an aware RFC-3339 timestamp without accepting a local wall clock."""
    if not isinstance(value, str) or _RFC3339_TIMESTAMP.fullmatch(value) is None:
        raise ToolError(_INVALID_TIMESTAMP_MESSAGE)

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ToolError(_INVALID_TIMESTAMP_MESSAGE) from error

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ToolError(_INVALID_TIMESTAMP_MESSAGE)
    return parsed


def main() -> None:
    """Run the server without writing a banner or diagnostics to stdout."""
    create_server().run(transport="stdio", show_banner=False)


__all__ = ["create_server", "main"]
