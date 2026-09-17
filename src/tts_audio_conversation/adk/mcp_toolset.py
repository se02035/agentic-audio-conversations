"""``McpToolset`` wrapper; listing retries come from ADK's ``retry_on_errors``."""

from __future__ import annotations

from typing import Any

from google.adk.tools.mcp_tool.mcp_toolset import McpToolset


class RetryingMcpToolset(McpToolset):
    """``McpToolset`` that lists tools via ADK's built-in ``retry_on_errors``."""

    async def get_tools(self, readonly_context: Any = None) -> list[Any]:
        """List MCP tools once; ``McpToolset`` retries transient session errors."""
        tools = await super().get_tools(readonly_context)
        return list(tools)
