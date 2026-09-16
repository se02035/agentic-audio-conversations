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

from tests.mcp.helpers import (
    instant_synth,
    mcp_app,
    mcp_settings,
    upload_and_start,
    write_fake_wav,
)
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

    mcp, service, gcs = mcp_app(slow_synth)
    manager = service._jobs
    async with Client(mcp) as client:
        t0 = time.perf_counter()
        result = await upload_and_start(client, sample_script_yaml)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.4
        assert result["status"] in {JobStatus.queued, JobStatus.running}
        assert result["job_id"]
        assert result["audio_uri"].startswith("gs://test-eu-bucket/conversation/jobs/")
        assert result["audio_uri"].endswith("/output/audio.wav")
        assert result["status_uri"].endswith("/status.json")
        assert "/jobs/" in result["status_uri"]
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
    """Invalid YAML at script_uri does not create a job."""
    mcp, service, gcs = mcp_app(instant_synth)
    manager = service._jobs
    uri = "gs://test-eu-bucket/conversation/scripts/bad/script.yaml"
    gcs.objects[uri] = b"invalid: ["
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("start_conversation", {"script_uri": uri})
    assert manager._jobs == {}


async def test_start_conversation_rejects_oversize(sample_script_yaml: str) -> None:
    """AUDIO_CONVERSATION_MAX_SCRIPT_BYTES is enforced on start_conversation."""
    mcp, service, gcs = mcp_app(
        instant_synth,
        settings=mcp_settings(audio_conversation_max_script_bytes=32),
    )
    manager = service._jobs
    uri = "gs://test-eu-bucket/conversation/scripts/big/script.yaml"
    gcs.objects[uri] = sample_script_yaml.encode()
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="bytes"):
            await client.call_tool("start_conversation", {"script_uri": uri})
    assert manager._jobs == {}


async def test_list_tools_has_no_download() -> None:
    """MCP never exposes a download tool; clients fetch WAV from GCS themselves."""
    mcp, _service, _gcs = mcp_app(instant_synth)
    async with Client(mcp) as client:
        tools = await client.list_tools()
    names = {tool.name for tool in tools}
    assert names == {
        "upload_script",
        "validate_script",
        "translate_script",
        "start_conversation",
        "get_conversation_status",
        "cancel_conversation",
    }
    assert "download" not in names


async def test_start_conversation_rejects_unknown_voice_without_job(
    sample_script_yaml: str,
) -> None:
    """list_voices failure does not enqueue a job or persist status.json."""
    from tts_audio_conversation.logic.exceptions import VoiceCatalogError

    def boom(_script: Any, _handles: Any) -> None:
        raise VoiceCatalogError("Voice(s) not found in EU Chirp 3 HD catalog")

    mcp, service, gcs = mcp_app(instant_synth, voice_catalog_fn=boom)
    manager = service._jobs
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="Chirp 3 HD"):
            await upload_and_start(client, sample_script_yaml)
    assert manager._jobs == {}
    assert not any(key.endswith("/status.json") for key in gcs.objects)
