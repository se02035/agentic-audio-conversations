"""E2E error-path coverage for MCP HTTP tools."""

from __future__ import annotations

import uuid

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from tests.e2e.mcp_live import live_mcp_http, tool_data


@pytest.mark.e2e
async def test_live_mcp_start_rejects_missing_script_uri() -> None:
    """start_conversation on a nonexistent gs:// URI returns a tool error."""
    suffix = uuid.uuid4().hex[:8]
    prefix = f"conversation/live_mcp_err_{suffix}"
    async with live_mcp_http(prefix) as live:
        missing = f"gs://{live.bucket}/{prefix}/scripts/missing/script.yaml"
        async with Client(live.url) as client:
            with pytest.raises(ToolError):
                await client.call_tool("start_conversation", {"script_uri": missing})


@pytest.mark.e2e
async def test_live_mcp_cancel_unknown_job() -> None:
    """cancel_conversation on an unknown id returns a tool error."""
    suffix = uuid.uuid4().hex[:8]
    prefix = f"conversation/live_mcp_cancel_err_{suffix}"
    async with live_mcp_http(prefix) as live:
        async with Client(live.url) as client:
            with pytest.raises(ToolError, match="Unknown job_id"):
                await client.call_tool(
                    "cancel_conversation",
                    {"job_id": "00000000-0000-0000-0000-000000000000"},
                )


@pytest.mark.e2e
async def test_live_mcp_validate_missing_is_soft() -> None:
    """validate_script soft-fails for a missing object (valid=False)."""
    suffix = uuid.uuid4().hex[:8]
    prefix = f"conversation/live_mcp_val_err_{suffix}"
    async with live_mcp_http(prefix) as live:
        missing = f"gs://{live.bucket}/{prefix}/scripts/missing/script.yaml"
        async with Client(live.url) as client:
            result = tool_data(await client.call_tool("validate_script", {"script_uri": missing}))
            assert result["valid"] is False
            assert result["error"]
