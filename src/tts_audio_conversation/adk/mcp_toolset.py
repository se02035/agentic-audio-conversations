"""``McpToolset`` that retries ``get_tools`` while MCP HTTP is starting."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

logger = logging.getLogger(__name__)


class RetryingMcpToolset(McpToolset):
    """``McpToolset`` that retries ``get_tools`` while MCP HTTP is starting."""

    def __init__(self, *args: Any, connect_attempts: int = 15, **kwargs: Any) -> None:
        """See ``McpToolset``; ``connect_attempts`` is the extra retry budget."""
        super().__init__(*args, **kwargs)
        self._connect_attempts = max(1, connect_attempts)

    async def get_tools(self, readonly_context: Any = None) -> list[Any]:
        """List MCP tools, retrying transient connect failures."""
        delay = 0.4
        last: Exception | None = None
        for attempt in range(self._connect_attempts):
            try:
                tools = await super().get_tools(readonly_context)
                return list(tools)
            except Exception as exc:
                last = exc
                if attempt + 1 >= self._connect_attempts:
                    break
                logger.info("MCP tool list failed (attempt %s): %s", attempt + 1, exc)
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, 2.0)
        assert last is not None
        raise last
