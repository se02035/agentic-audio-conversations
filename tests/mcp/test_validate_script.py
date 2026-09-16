"""Unit tests for the ``validate_script`` MCP tool."""

from __future__ import annotations

from fastmcp import Client

from tests.mcp.helpers import instant_synth, mcp_app, mcp_settings, tool_data


async def test_validate_script_accepts_yaml(sample_script_yaml: str) -> None:
    """Valid YAML returns schema metrics and does not start a job."""
    mcp, manager, gcs = mcp_app(instant_synth)
    async with Client(mcp) as client:
        result = tool_data(
            await client.call_tool("validate_script", {"script": sample_script_yaml})
        )
    assert result["valid"] is True
    assert result["title"] == "Tech Pulse Europe"
    assert result["turn_count"] >= 1
    assert result["error"] is None
    assert manager._jobs == {}
    assert gcs.objects == {}


async def test_validate_script_rejects_invalid_yaml() -> None:
    """Broken YAML returns valid=False with an error string."""
    mcp, _manager, gcs = mcp_app(instant_synth)
    async with Client(mcp) as client:
        result = tool_data(await client.call_tool("validate_script", {"script": "not: [yaml"}))
    assert result["valid"] is False
    assert result["error"]
    assert gcs.objects == {}


async def test_validate_script_rejects_oversize(sample_script_yaml: str) -> None:
    """Byte cap applies to validate_script as well as start_conversation."""
    mcp, _manager, _gcs = mcp_app(
        instant_synth,
        settings=mcp_settings(audio_conversation_max_script_bytes=32),
    )
    async with Client(mcp) as client:
        result = tool_data(
            await client.call_tool("validate_script", {"script": sample_script_yaml})
        )
    assert result["valid"] is False
    assert "bytes" in (result["error"] or "")


async def test_validate_script_rejects_non_chirp3_without_list_voices() -> None:
    """Schema Chirp3-HD check does not call Cloud TTS."""
    payload = """
metadata:
  title: Bad Voice
  language_code: en-US
voices:
  host:
    name: en-US-Standard-A
    language_code: en-US
turns:
  - speaker: host
    text: Hello.
"""
    mcp, manager, gcs = mcp_app(instant_synth)
    async with Client(mcp) as client:
        result = tool_data(await client.call_tool("validate_script", {"script": payload}))
    assert result["valid"] is False
    assert "Chirp3-HD" in (result["error"] or "")
    assert manager._jobs == {}
    assert gcs.objects == {}
