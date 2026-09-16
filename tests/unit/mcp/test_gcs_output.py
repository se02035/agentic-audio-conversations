"""Unit tests for MCP GCS outputs (client download is from GCS, not an MCP tool)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastmcp import Client

from tests.unit.mcp.helpers import instant_synth, mcp_app, upload_and_start
from tts_audio_conversation.mcp.jobs import JobStatus


async def test_succeeded_job_writes_audio_and_status_to_gcs(sample_script_yaml: str) -> None:
    """After success, FakeGcs holds audio.wav and status.json at the returned URIs."""
    mcp, _service, gcs = mcp_app(instant_synth)
    async with Client(mcp) as client:
        started = await upload_and_start(client, sample_script_yaml)
        deadline = time.monotonic() + 5
        status: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            from tests.unit.mcp.helpers import tool_data

            status = tool_data(
                await client.call_tool("get_conversation_status", {"job_id": started["job_id"]})
            )
            if status["status"] == JobStatus.succeeded:
                break
            await asyncio.sleep(0.05)
        assert status is not None
        assert status["status"] == JobStatus.succeeded

    audio = gcs.download_bytes(None, started["audio_uri"])
    sidecar = gcs.download_bytes(None, started["status_uri"])
    assert audio == b"RIFFFAKE"
    assert sidecar is not None
    assert b"succeeded" in sidecar
