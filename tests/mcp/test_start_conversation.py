"""Unit tests for the ``start_conversation`` MCP tool."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from tests.mcp.helpers import instant_synth, mcp_app, mcp_settings, tool_data, write_fake_wav
from tts_audio_conversation.mcp.jobs import JobStatus


async def test_start_conversation_returns_immediately_with_gcs_uris(
    sample_script_yaml: str,
) -> None:
    """start_conversation returns job_id and gs:// URIs without waiting for TTS."""
    started = threading.Event()
    release = threading.Event()

    def slow_synth(_client: Any, _script: Any, output_path: Path, **_kwargs: Any) -> Path:
        started.set()
        assert release.wait(timeout=5)
        return write_fake_wav(output_path)

    mcp, manager, gcs = mcp_app(slow_synth)
    async with Client(mcp) as client:
        t0 = time.perf_counter()
        result = tool_data(
            await client.call_tool("start_conversation", {"script": sample_script_yaml})
        )
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.4
        assert result["status"] in {JobStatus.queued, JobStatus.running}
        assert result["job_id"]
        assert result["audio_uri"].startswith("gs://test-eu-bucket/conversation/")
        assert result["audio_uri"].endswith("/audio.wav")
        assert result["status_uri"].endswith("/status.json")
        persisted = json.loads(gcs.objects[result["status_uri"]].decode("utf-8"))
        assert persisted["status"] in {JobStatus.queued, JobStatus.running}
        assert persisted["job_id"] == result["job_id"]
        assert persisted.get("updated_at")
        assert await asyncio.to_thread(started.wait, 2)
        release.set()
    assert manager._jobs[result["job_id"]].status in {
        JobStatus.queued,
        JobStatus.running,
        JobStatus.succeeded,
    }


async def test_start_conversation_rejects_invalid_yaml() -> None:
    """Invalid YAML does not create a job."""
    mcp, manager, _gcs = mcp_app(instant_synth)
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("start_conversation", {"script": "invalid: ["})
    assert manager._jobs == {}


async def test_start_conversation_rejects_oversize(sample_script_yaml: str) -> None:
    """AUDIO_CONVERSATION_MAX_SCRIPT_BYTES is enforced on start_conversation."""
    mcp, manager, _gcs = mcp_app(
        instant_synth,
        settings=mcp_settings(audio_conversation_max_script_bytes=32),
    )
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="bytes"):
            await client.call_tool("start_conversation", {"script": sample_script_yaml})
    assert manager._jobs == {}


async def test_list_tools_has_no_download() -> None:
    """MCP never exposes a download tool; clients fetch WAV from GCS themselves."""
    mcp, _manager, _gcs = mcp_app(instant_synth)
    async with Client(mcp) as client:
        tools = await client.list_tools()
    names = {tool.name for tool in tools}
    assert names == {
        "start_conversation",
        "get_conversation_status",
        "cancel_conversation",
        "validate_script",
    }
    assert "download" not in names


async def test_start_conversation_rejects_unknown_voice_without_job(
    sample_script_yaml: str,
) -> None:
    """list_voices failure does not enqueue a job or persist status.json."""
    from tts_audio_conversation.logic.exceptions import VoiceCatalogError

    def boom(_script: Any, _handles: Any) -> None:
        raise VoiceCatalogError("Voice(s) not found in EU Chirp 3 HD catalog")

    mcp, manager, gcs = mcp_app(instant_synth, voice_catalog_fn=boom)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="Chirp 3 HD"):
            await client.call_tool("start_conversation", {"script": sample_script_yaml})
    assert manager._jobs == {}
    assert gcs.objects == {}
