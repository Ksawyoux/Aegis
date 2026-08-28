"""MCP transport wiring: spawn the stdio server and adapt its tools.

This module is the seam between the agent loop, which owns capture and turn
accounting, and the MCP server subprocess, which owns database access. Keeping
it separate means the loop stays testable with injected fakes while production
callers get real transport from one place. Each MCP tool is adapted to the
OpenAI Responses API function-tool shape -- a flat ``{"type": "function",
"name", "description", "parameters"}`` object -- and exposes an ``invoke``
the loop calls with parsed arguments.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import TextContent

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
    environment["AEGIS_OPENAI_BASE_URL"] = settings.openai_base_url
    environment["AEGIS_EMBEDDING_MODEL"] = settings.embedding_model
    environment["AEGIS_EMBEDDING_DIM"] = str(settings.embedding_dim)
    if settings.openai_api_key is not None:
        environment["OPENAI_API_KEY"] = settings.openai_api_key.get_secret_value()
    return environment


@dataclass(frozen=True)
class McpFunctionTool:
    """One registered MCP tool, executable through its spawning session."""

    name: str
    description: str
    parameters: dict[str, Any]
    session: ClientSession

    def as_openai_tool(self) -> dict[str, Any]:
        """Return the Responses API function-tool definition for this tool."""
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    async def invoke(self, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Call the tool and return ``(result_text, is_error)``.

        The text is the concatenation of the result's text blocks; a result
        carrying no text blocks yields an empty JSON object so a malformed
        success payload fails envelope validation loudly instead of on
        ``json.loads("")``.
        """
        result = await self.session.call_tool(self.name, arguments)
        text = "".join(
            block.text for block in result.content if isinstance(block, TextContent)
        )
        return (text if text else "{}"), bool(result.isError)


@asynccontextmanager
async def mcp_tools(settings: Settings) -> AsyncIterator[list[McpFunctionTool]]:
    """Spawn the stdio MCP server and yield its tools adapted for the loop.

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
            yield [
                McpFunctionTool(
                    name=tool.name,
                    description=tool.description or "",
                    parameters=(
                        tool.inputSchema
                        if isinstance(tool.inputSchema, dict)
                        else {"type": "object", "properties": {}}
                    ),
                    session=session,
                )
                for tool in listed.tools
            ]


__all__ = ["McpFunctionTool", "PROJECT_ROOT", "mcp_tools"]
