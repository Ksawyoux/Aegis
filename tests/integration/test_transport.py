"""Composition tests for the MCP transport seam.

These exist because a gap here is invisible to every per-package test: the
agent loop is tested with injected fake tools, and the application boundary is
tested with a stubbed agent, so both suites pass while nothing connects them.
An earlier revision shipped exactly that -- ``investigate`` supplied no tools
and held a subprocess handle that was never assigned.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from aegis.agent.transport import PROJECT_ROOT, _server_environment, mcp_tools
from aegis.config import Settings
from aegis.db.models import Service


@pytest.mark.asyncio
async def test_transport_adapts_exactly_the_registered_tools() -> None:
    async with mcp_tools(Settings()) as tools:
        assert len(tools) == 3


@pytest.mark.asyncio
async def test_investigate_supplies_tools_to_the_agent() -> None:
    """The composition assertion: a real tool list reaches ``run_agent``.

    Without this, ``tools`` defaulting to an empty tuple leaves the agent with
    nothing to call, so it cites nothing and every run fails provenance -- while
    looking like a citation bug rather than missing transport.
    """
    captured: dict[str, object] = {}

    async def fake_run_agent(brief, run_context, settings, *, tools=()):  # type: ignore[no-untyped-def]
        captured["tools"] = list(tools)
        raise RuntimeError("stop after capturing tools")

    from aegis.app import investigate as module

    original = module.run_agent
    module.run_agent = fake_run_agent  # type: ignore[assignment]
    try:
        # Raised through two nested anyio task groups, so it arrives wrapped.
        with pytest.raises(BaseException) as caught:
            await module._run_with_transport("brief", _null_context(), Settings())
        assert isinstance(module._unwrap_solo_group(caught.value), RuntimeError)
    finally:
        module.run_agent = original  # type: ignore[assignment]

    assert len(captured["tools"]) == 3  # type: ignore[arg-type]


def test_server_environment_pins_the_configured_database_url() -> None:
    """``StdioServerParameters`` does not inherit the parent environment.

    The MCP SDK passes a minimal safe environment, so without an explicit value
    the server falls back to the default ``database_url`` and silently reads a
    different database -- surfacing as an empty tool result, not an error.
    """
    from aegis.agent.transport import _server_environment

    settings = Settings(database_url="postgresql+psycopg://u:p@127.0.0.1:5999/other")
    environment = _server_environment(settings)
    assert environment["AEGIS_DATABASE_URL"] == settings.database_url
    assert environment["AEGIS_DATABASE_URL"] != Settings.model_fields["database_url"].default


@pytest.mark.asyncio
async def test_spawned_server_reads_the_callers_database(migrated_engine: Engine) -> None:
    """The child must answer a question only the caller's database can answer.

    An earlier version listed the child's tools and then read the marker back
    through the *parent* engine, which proves nothing: a child pointed at any
    database, or at none, still advertises three tools and the parent still sees
    its own row. The tool call below is the discriminator -- a server on a
    different database has never heard of this service.
    """
    marker = "transport-probe-service"
    with Session(migrated_engine) as session, session.begin():
        session.add(Service(name=marker))

    settings = Settings(database_url=os.environ["AEGIS_DATABASE_URL"])
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "aegis.mcp_server"],
        cwd=PROJECT_ROOT,
        env=_server_environment(settings),
    )
    window = datetime(2026, 8, 20, tzinfo=UTC)
    try:
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session_rpc:
                await session_rpc.initialize()
                response = await session_rpc.call_tool(
                    "get_error_telemetry",
                    {
                        "service": marker,
                        "window_start": window.isoformat().replace("+00:00", "Z"),
                        "window_end": (window + timedelta(minutes=1))
                        .isoformat()
                        .replace("+00:00", "Z"),
                    },
                )
        assert response.isError is False, (
            "the spawned server could not resolve a service that exists in the caller's "
            "database, so it is reading a different one"
        )
    finally:
        with Session(migrated_engine) as session, session.begin():
            found = session.query(Service).filter_by(name=marker).one_or_none()
            if found is not None:
                session.delete(found)


def test_declared_exceptions_survive_nested_task_groups() -> None:
    """The unwrapper is what makes investigate()'s propagation contract true."""
    from aegis.agent.summary import ProvenanceError
    from aegis.app.investigate import _unwrap_solo_group

    inner = ProvenanceError("fabricated citation")
    nested = BaseExceptionGroup("outer", [BaseExceptionGroup("inner", [inner])])
    assert _unwrap_solo_group(nested) is inner

    # A genuine multi-failure group must not be collapsed into one of its members.
    multi = BaseExceptionGroup("outer", [inner, ValueError("second")])
    assert _unwrap_solo_group(multi) is multi


def _null_context() -> object:
    class _Ctx:
        captured_cites: set[str] = set()

        def emit(self, event: object) -> None:
            return None

        def capture_tool_result(self, tool: str, args: dict[str, object], result: object) -> None:
            return None

    return _Ctx()
