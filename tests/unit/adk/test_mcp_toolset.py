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


def _toolset(connect_attempts: int = 5) -> RetryingMcpToolset:
    """Build a toolset pointed at a closed port (``get_tools`` is patched in tests)."""
    return RetryingMcpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url="http://127.0.0.1:9/mcp",
            timeout=1.0,
        ),
        tool_filter=list(LLM_VISIBLE_MCP_TOOLS),
        connect_attempts=connect_attempts,
    )


async def test_get_tools_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Transient ``get_tools`` failures retry with backoff, then return tools."""
    attempts = {"n": 0}
    sleeps: list[float] = []

    async def fake_get_tools(self: McpToolset, readonly_context: Any = None) -> list[Any]:
        _ = self, readonly_context
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionRefusedError("mcp not listening")
        return [MagicMock(name="validate_script")]

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(McpToolset, "get_tools", fake_get_tools)
    monkeypatch.setattr("tts_audio_conversation.adk.mcp_toolset.asyncio.sleep", fake_sleep)
    tools = await _toolset(connect_attempts=5).get_tools()
    assert len(tools) == 1
    assert attempts["n"] == 3
    assert sleeps[0] == pytest.approx(0.4)
    assert sleeps[1] == pytest.approx(0.6)
    assert len(sleeps) == 2


async def test_get_tools_raises_after_attempts_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The last connect error is re-raised when the retry budget is spent."""

    async def fake_get_tools(self: McpToolset, readonly_context: Any = None) -> list[Any]:
        _ = self, readonly_context
        raise ConnectionError("still down")

    async def fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(McpToolset, "get_tools", fake_get_tools)
    monkeypatch.setattr("tts_audio_conversation.adk.mcp_toolset.asyncio.sleep", fake_sleep)
    with pytest.raises(ConnectionError, match="still down"):
        await _toolset(connect_attempts=3).get_tools()


async def test_get_tools_does_not_sleep_when_attempts_is_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single attempt fails immediately without sleeping."""
    sleeps: list[float] = []

    async def fake_get_tools(self: McpToolset, readonly_context: Any = None) -> list[Any]:
        _ = self, readonly_context
        raise TimeoutError("no connect")

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(McpToolset, "get_tools", fake_get_tools)
    monkeypatch.setattr("tts_audio_conversation.adk.mcp_toolset.asyncio.sleep", fake_sleep)
    with pytest.raises(TimeoutError):
        await _toolset(connect_attempts=1).get_tools()
    assert sleeps == []


async def test_retrying_toolset_hides_start_and_upload() -> None:
    """Against mocked MCP HTTP, the LLM-visible filter matches production."""
    async with mocked_mcp_http(delayed_synth(0.0)) as live:
        toolset = RetryingMcpToolset(
            connection_params=StreamableHTTPConnectionParams(url=live.url, timeout=10.0),
            tool_filter=list(LLM_VISIBLE_MCP_TOOLS),
            connect_attempts=5,
        )
        try:
            tools = await toolset.get_tools()
            names = {tool.name for tool in tools}
            assert names == set(LLM_VISIBLE_MCP_TOOLS)
            assert names.isdisjoint(set(EXCLUDED_MCP_TOOLS))
        finally:
            await toolset.close()
