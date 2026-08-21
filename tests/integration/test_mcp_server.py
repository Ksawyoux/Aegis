from __future__ import annotations

import sys
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession
from mcp.client import stdio as mcp_stdio
from mcp.client.stdio import StdioServerParameters, stdio_client
from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session

from aegis.config import Settings
from aegis.db.models import Service
from aegis.mcp_server.schemas import ErrorTelemetry

_SERVICE_NAME = "mcp-server-test-service"
_WINDOW_START = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)


@pytest.fixture
def seeded_service(migrated_engine: Engine) -> Generator[str]:
    """Commit data that is visible to the separately spawned MCP process."""
    with Session(migrated_engine) as session:
        session.execute(delete(Service).where(Service.name == _SERVICE_NAME))
        session.add(Service(name=_SERVICE_NAME))
        session.commit()

    try:
        yield _SERVICE_NAME
    finally:
        with Session(migrated_engine) as session:
            session.execute(delete(Service).where(Service.name == _SERVICE_NAME))
            session.commit()


@pytest.mark.asyncio
async def test_mcp_server_stdio_boundary(
    monkeypatch: pytest.MonkeyPatch, seeded_service: str
) -> None:
    stdout_lines: list[str] = []
    original_validate = mcp_stdio.types.JSONRPCMessage.model_validate_json

    def capture_protocol_line(value: str, *args: Any, **kwargs: Any) -> Any:
        stdout_lines.append(value)
        return original_validate(value, *args, **kwargs)

    monkeypatch.setattr(
        mcp_stdio.types.JSONRPCMessage, "model_validate_json", capture_protocol_line
    )
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "aegis.mcp_server"],
        cwd=Path(__file__).parents[2],
        env={"AEGIS_DATABASE_URL": Settings().database_url},
    )

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "get_error_telemetry",
                "get_incident_diff",
                "search_similar_postmortems",
            }
            assert len(tools.tools) == 3

            response = await session.call_tool(
                "get_error_telemetry", _valid_telemetry_arguments(seeded_service)
            )
            assert response.isError is False
            assert response.structuredContent is not None
            ErrorTelemetry.model_validate(response.structuredContent)

            query_error = await session.call_tool(
                "get_error_telemetry", _valid_telemetry_arguments("missing-service")
            )
            assert query_error.isError is True

            later_response = await session.call_tool(
                "get_error_telemetry", _valid_telemetry_arguments(seeded_service)
            )
            assert later_response.isError is False

            naive_timestamp = await session.call_tool(
                "get_error_telemetry",
                {
                    **_valid_telemetry_arguments(seeded_service),
                    "window_start": "2026-08-20T10:00:00",
                },
            )
            assert naive_timestamp.isError is True

    # stdio_client hands every complete stdout line to this parser.  A banner,
    # print, or SQL echo line is retained here and fails JSON-RPC validation.
    assert stdout_lines
    for line in stdout_lines:
        original_validate(line)


def _valid_telemetry_arguments(service: str) -> dict[str, object]:
    return {
        "service": service,
        "window_start": _WINDOW_START.isoformat().replace("+00:00", "Z"),
        "window_end": (_WINDOW_START + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
    }
