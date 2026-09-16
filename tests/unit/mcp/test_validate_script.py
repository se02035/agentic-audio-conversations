"""Unit tests for the ``validate_script`` MCP tool."""

from __future__ import annotations

from fastmcp import Client

from tests.unit.mcp.helpers import instant_synth, mcp_app, mcp_settings, tool_data


async def test_validate_script_accepts_yaml(sample_script_yaml: str) -> None:
    """Valid YAML returns schema metrics and does not start a job."""
    mcp, service, _gcs = mcp_app(instant_synth)
    async with Client(mcp) as client:
        uploaded = tool_data(
            await client.call_tool("upload_script", {"script": sample_script_yaml})
        )
        result = tool_data(
            await client.call_tool("validate_script", {"script_uri": uploaded["script_uri"]})
        )
    assert result["valid"] is True
    assert result["title"] == "Tech Pulse Europe"
    assert result["turn_count"] >= 1
    assert result["error"] is None
    assert result["script_uri"] == uploaded["script_uri"]
    assert service._jobs._jobs == {}


async def test_validate_script_rejects_missing_uri() -> None:
    """Missing object returns valid=False with an error string."""
    mcp, _service, _gcs = mcp_app(instant_synth)
    async with Client(mcp) as client:
        result = tool_data(
            await client.call_tool(
                "validate_script",
                {"script_uri": "gs://test-eu-bucket/conversation/scripts/missing/script.yaml"},
            )
        )
    assert result["valid"] is False
    assert result["error"]


async def test_validate_script_rejects_invalid_yaml() -> None:
    """Broken YAML at a URI returns valid=False."""
    mcp, service, gcs = mcp_app(instant_synth)
    uri = "gs://test-eu-bucket/conversation/scripts/bad/script.yaml"
    gcs.objects[uri] = b"not: [yaml"
    async with Client(mcp) as client:
        result = tool_data(await client.call_tool("validate_script", {"script_uri": uri}))
    assert result["valid"] is False
    assert result["error"]
    assert service._jobs._jobs == {}


async def test_validate_script_rejects_oversize(sample_script_yaml: str) -> None:
    """Byte cap applies when validating an uploaded object."""
    mcp, _service, gcs = mcp_app(
        instant_synth,
        settings=mcp_settings(audio_conversation_max_script_bytes=32),
    )
    # Bypass upload_script (which also enforces the cap) and plant an oversized object.
    uri = "gs://test-eu-bucket/conversation/scripts/big/script.yaml"
    gcs.objects[uri] = sample_script_yaml.encode("utf-8")
    async with Client(mcp) as client:
        result = tool_data(await client.call_tool("validate_script", {"script_uri": uri}))
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
    mcp, service, _gcs = mcp_app(instant_synth)
    async with Client(mcp) as client:
        # upload_script raises ToolError for schema failures — plant bytes instead.
        uri = "gs://test-eu-bucket/conversation/scripts/badvoice/script.yaml"
        service._storage.upload_bytes(uri, payload.encode("utf-8"), "application/x-yaml")
        result = tool_data(await client.call_tool("validate_script", {"script_uri": uri}))
    assert result["valid"] is False
    assert "Chirp3-HD" in (result["error"] or "")
    assert service._jobs._jobs == {}
