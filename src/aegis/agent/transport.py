"""MCP transport wiring: spawn the stdio server and adapt its tools.

This module is the seam between the agent loop, which owns capture and turn
accounting, and the MCP server subprocess, which owns database access. Keeping
it separate means the loop stays testable with injected fakes while production
callers get real transport from one place.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from anthropic.lib.tools.mcp import async_mcp_tool
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from aegis.config import Settings

PROJECT_ROOT = Path(__file__).parents[3]


def _server_environment(settings: Settings) -> dict[str, str]:
    """Return the environment the server subprocess needs.

    ``StdioServerParameters`` does not inherit the parent environment: the MCP
    SDK passes a minimal safe environment instead. Without this, the server
    silently falls back to the default ``database_url`` and reads a different
    database than the caller configured -- which presents as an empty or
    unexpected tool result rather than as a configuration error.
    """
    environment = dict(os.environ)
    environment["AEGIS_DATABASE_URL"] = settings.database_url
    return environment


@asynccontextmanager
async def mcp_tools(settings: Settings) -> AsyncIterator[list[Any]]:
    """Spawn the stdio MCP server and yield its tools adapted for the runner.

    The subprocess lifetime is the lifetime of this context manager, so leaving
    it -- normally or by exception -- tears the server down. Callers therefore
    get the teardown-on-every-path guarantee from the context manager itself
    rather than from a separate handle they must remember to terminate.
    """
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "aegis.mcp_server"],
        cwd=PROJECT_ROOT,
        env=_server_environment(settings),
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            yield [async_mcp_tool(tool, session) for tool in listed.tools]


__all__ = ["mcp_tools", "PROJECT_ROOT"]
