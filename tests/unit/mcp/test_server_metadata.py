"""MCP server instructions and tool annotation metadata."""

from __future__ import annotations

from fastmcp import Client

from tests.unit.mcp.helpers import instant_synth, mcp_app

EXPECTED_TOOLS = {
    "upload_script",
    "validate_script",
    "translate_script",
    "start_conversation",
    "get_conversation_status",
    "cancel_conversation",
}

# read_only, destructive, idempotent — matches server._annotations.
EXPECTED_HINTS: dict[str, tuple[bool, bool, bool]] = {
    "upload_script": (False, False, False),
    "validate_script": (True, False, True),
    "translate_script": (False, False, True),
    "start_conversation": (False, True, False),
    "get_conversation_status": (True, False, True),
    "cancel_conversation": (False, True, True),
}


async def test_server_instructions_describe_workflow() -> None:
    """Initialize result includes client instructions for the job workflow."""
    mcp, _service, _gcs = mcp_app(instant_synth)
    async with Client(mcp) as client:
        text = client.instructions or ""
    assert "upload_script" in text
    assert "validate_script" in text
    assert "start_conversation" in text
    assert "get_conversation_status" in text
    assert "no download tool" in text.lower()


async def test_list_tools_annotations_match_hints() -> None:
    """Each tool advertises the read-only / destructive / idempotent hints."""
    mcp, _service, _gcs = mcp_app(instant_synth)
    async with Client(mcp) as client:
        tools = await client.list_tools()
    names = {tool.name for tool in tools}
    assert names == EXPECTED_TOOLS
    assert "download" not in names
    by_name = {tool.name: tool for tool in tools}
    for name, (read_only, destructive, idempotent) in EXPECTED_HINTS.items():
        hints = by_name[name].annotations
        assert hints is not None, name
        assert hints.read_only_hint is read_only, name
        assert hints.destructive_hint is destructive, name
        assert hints.idempotent_hint is idempotent, name
        assert hints.open_world_hint is True, name
