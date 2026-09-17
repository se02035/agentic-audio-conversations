"""Unit tests for ``build_app`` wiring (no Gemini call, no api_server)."""

from __future__ import annotations

import pytest

pytest.importorskip("google.adk")

from tts_audio_conversation.adk.config import AgentSettings  # noqa: E402
from tts_audio_conversation.adk.create_audio import CREATE_AUDIO_TOOL_NAME  # noqa: E402
from tts_audio_conversation.adk.mcp_client import get_mcp_client  # noqa: E402
from tts_audio_conversation.adk.mcp_toolset import RetryingMcpToolset  # noqa: E402


def test_build_app_uses_long_running_create_and_retrying_toolset() -> None:
    """``build_app`` is an LlmAgent App with ingest, LRO create, and MCP toolset."""
    from tts_audio_conversation.adk.agent import AGENT_NAME, APP_NAME, build_app

    settings = AgentSettings(
        audio_conversation_mcp_url="http://127.0.0.1:9/mcp",
        adk_agent_model="gemini-3.8-flash",
    )
    built = build_app(settings, download_bytes_fn=lambda _uri: b"RIFF")
    assert built.app.name == APP_NAME
    assert built.root_agent.name == AGENT_NAME
    assert built.root_agent.model.model == "gemini-3.8-flash"  # type: ignore[union-attr]
    assert built.lro_plugin is not None
    tool_names: list[str] = []
    lro_found = False
    retrying_found = False
    for tool in built.root_agent.tools:
        if isinstance(tool, RetryingMcpToolset):
            retrying_found = True
        name = getattr(tool, "name", None)
        if name:
            tool_names.append(str(name))
        if callable(tool) and hasattr(tool, "__name__"):
            tool_names.append(str(tool.__name__))
        func = getattr(tool, "func", None) or getattr(tool, "_func", None)
        if callable(func):
            tool_names.append(str(getattr(func, "__name__", "")))
        if getattr(tool, "is_long_running", False):
            lro_found = True
            assert CREATE_AUDIO_TOOL_NAME in (
                getattr(tool, "name", ""),
                getattr(getattr(tool, "func", None), "__name__", ""),
            )
    assert lro_found
    assert retrying_found
    assert "ingest_uploaded_script" in tool_names


def test_build_app_does_not_install_process_wide_mcp_client() -> None:
    """Each app keeps its own client; a second build must not redirect the first."""
    from tts_audio_conversation.adk.agent import build_app

    first = build_app(
        AgentSettings(audio_conversation_mcp_url="http://127.0.0.1:11/mcp"),
        download_bytes_fn=lambda _uri: None,
    )
    second = build_app(
        AgentSettings(audio_conversation_mcp_url="http://127.0.0.1:12/mcp"),
        download_bytes_fn=lambda _uri: None,
    )
    assert first.lro_plugin._client.url == "http://127.0.0.1:11/mcp"
    assert second.lro_plugin._client.url == "http://127.0.0.1:12/mcp"
    assert first.lro_plugin._client is not second.lro_plugin._client
    assert get_mcp_client() is not first.lro_plugin._client
    assert get_mcp_client() is not second.lro_plugin._client
