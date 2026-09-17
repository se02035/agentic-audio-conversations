"""Unit tests for ``RetryingMcpToolset`` (no ``adk api_server``)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from tests.unit.adk.helpers import delayed_synth, mocked_mcp_http
from tts_audio_conversation.adk.mcp_client import EXCLUDED_MCP_TOOLS, LLM_VISIBLE_MCP_TOOLS

pytest.importorskip("google.adk")

from google.adk.tools.mcp_tool.mcp_session_manager import (  # noqa: E402
    StreamableHTTPConnectionParams,
)
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset  # noqa: E402

from tts_audio_conversation.adk.mcp_toolset import RetryingMcpToolset  # noqa: E402


def _toolset() -> RetryingMcpToolset:
    """Build a toolset pointed at a closed port (``get_tools`` is patched in tests)."""
    return RetryingMcpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url="http://127.0.0.1:9/mcp",
            timeout=1.0,
        ),
        tool_filter=list(LLM_VISIBLE_MCP_TOOLS),
    )


async def test_get_tools_calls_super_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Listing does not add a retry layer on top of ``McpToolset``."""
    attempts = {"n": 0}

    async def fake_get_tools(self: McpToolset, readonly_context: Any = None) -> list[Any]:
        _ = self, readonly_context
        attempts["n"] += 1
        raise ConnectionRefusedError("mcp not listening")

    monkeypatch.setattr(McpToolset, "get_tools", fake_get_tools)
    with pytest.raises(ConnectionRefusedError, match="mcp not listening"):
        await _toolset().get_tools()
    assert attempts["n"] == 1


async def test_get_tools_returns_super_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful ``McpToolset.get_tools`` result is returned as a list."""
    listed = [MagicMock(name="validate_script")]

    async def fake_get_tools(self: McpToolset, readonly_context: Any = None) -> list[Any]:
        _ = self, readonly_context
        return listed

    monkeypatch.setattr(McpToolset, "get_tools", fake_get_tools)
    tools = await _toolset().get_tools()
    assert tools == listed
    assert tools is not listed


async def test_retrying_toolset_hides_start_and_upload() -> None:
    """Against mocked MCP HTTP, the LLM-visible filter matches production."""
    async with mocked_mcp_http(delayed_synth(0.0)) as live:
        toolset = RetryingMcpToolset(
            connection_params=StreamableHTTPConnectionParams(url=live.url, timeout=10.0),
            tool_filter=list(LLM_VISIBLE_MCP_TOOLS),
        )
        try:
            tools = await toolset.get_tools()
            names = {tool.name for tool in tools}
            assert names == set(LLM_VISIBLE_MCP_TOOLS)
            assert names.isdisjoint(set(EXCLUDED_MCP_TOOLS))
        finally:
            await toolset.close()
