"""Unit tests for the ``get_podcast_status`` MCP tool."""

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

from tests.mcp.helpers import instant_synth, mcp_app, tool_data, write_fake_wav
from tts_podcast_creator.mcp.jobs import JobStatus


async def _wait_for_status(
    client: Any,
    job_id: str,
    wanted: JobStatus,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Poll get_podcast_status until ``wanted`` or raise."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = tool_data(await client.call_tool("get_podcast_status", {"job_id": job_id}))
        if last["status"] == wanted:
            return last
        await asyncio.sleep(0.05)
    raise AssertionError(f"status did not become {wanted}; last={last}")


async def test_get_podcast_status_unknown_job_id() -> None:
    """Unknown ids return a not-found tool error, not a hang."""
    mcp, _manager, _gcs = mcp_app(instant_synth)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="Unknown job_id"):
            await client.call_tool("get_podcast_status", {"job_id": "missing"})


async def test_get_podcast_status_tracks_running_progress_then_succeeded(
    sample_script_yaml: str,
) -> None:
    """Polling reports running + batch progress, then succeeded."""
    release = threading.Event()
    entered = threading.Event()

    def gated_synth(_client: Any, _script: Any, output_path: Path, **kwargs: Any) -> Path:
        on_progress = kwargs.get("on_progress")
        entered.set()
        if on_progress is not None:
            on_progress(1, 3)
        assert release.wait(timeout=5)
        if on_progress is not None:
            on_progress(3, 3)
        return write_fake_wav(output_path)

    mcp, _manager, _gcs = mcp_app(gated_synth)
    async with Client(mcp) as client:
        started = tool_data(await client.call_tool("start_podcast", {"script": sample_script_yaml}))
        job_id = started["job_id"]
        assert await asyncio.to_thread(entered.wait, 2)
        running = await _wait_for_status(client, job_id, JobStatus.running)
        assert running["progress"]
        assert "batch" in running["progress"]
        sidecar = json.loads(_gcs.objects[started["status_uri"]].decode("utf-8"))
        assert "batch" in (sidecar.get("progress") or "")
        release.set()
        done = await _wait_for_status(client, job_id, JobStatus.succeeded)
        assert done["error"] is None
        assert done["audio_uri"] == started["audio_uri"]


async def test_get_podcast_status_reads_gcs_when_job_evicted_from_memory(
    sample_script_yaml: str,
) -> None:
    """After process-local state is dropped, status.json in GCS still answers polls."""
    mcp, manager, gcs = mcp_app(instant_synth)
    async with Client(mcp) as client:
        started = tool_data(await client.call_tool("start_podcast", {"script": sample_script_yaml}))
        job_id = started["job_id"]
        await _wait_for_status(client, job_id, JobStatus.succeeded)
        with manager._lock:
            assert job_id in manager._jobs
            del manager._jobs[job_id]
        restored = tool_data(await client.call_tool("get_podcast_status", {"job_id": job_id}))
    assert restored["status"] == JobStatus.succeeded
    assert restored["job_id"] == job_id
    raw = gcs.objects[started["status_uri"]]
    assert json.loads(raw.decode("utf-8"))["status"] == JobStatus.succeeded


async def test_get_podcast_status_fails_stale_running_without_resume() -> None:
    """Missing/old heartbeat on queued/running becomes failed; no TTS worker starts."""
    from tests.mcp.helpers import job_manager

    manager, gcs = job_manager(instant_synth)
    job_id = "stale-job"
    prefix = manager.settings.job_prefix_uri(job_id)
    status_uri = f"{prefix}/status.json"
    payload = {
        "job_id": job_id,
        "status": "running",
        "audio_uri": f"{prefix}/audio.wav",
        "status_uri": status_uri,
        "error": None,
        "progress": "batch 1/9",
    }
    gcs.objects[status_uri] = json.dumps(payload).encode("utf-8")
    restored = await manager.get_status(job_id)
    assert restored.status == JobStatus.failed
    assert restored.error
    assert "heartbeat" in restored.error.lower() or "crashed" in restored.error.lower()
    stored = json.loads(gcs.objects[status_uri].decode("utf-8"))
    assert stored["status"] == "failed"


async def test_terminal_jobs_are_pruned_from_memory(sample_script_yaml: str) -> None:
    """In-memory map does not retain terminal jobs past the prune TTL."""
    from datetime import timedelta

    from tests.mcp.helpers import mcp_settings
    from tts_podcast_creator.mcp.jobs import utc_now

    mcp, manager, _gcs = mcp_app(
        instant_synth,
        settings=mcp_settings(podcast_job_prune_ttl_sec=60),
    )
    async with Client(mcp) as client:
        started = tool_data(await client.call_tool("start_podcast", {"script": sample_script_yaml}))
        await _wait_for_status(client, started["job_id"], JobStatus.succeeded)
        job_id = started["job_id"]
        assert job_id in manager._jobs
        with manager._lock:
            current = manager._jobs[job_id]
            manager._jobs[job_id] = current.model_copy(
                update={"updated_at": utc_now() - timedelta(hours=2)}
            )
        manager._prune()
        assert job_id not in manager._jobs
        restored = tool_data(await client.call_tool("get_podcast_status", {"job_id": job_id}))
    assert restored["status"] == JobStatus.succeeded
    assert job_id not in manager._jobs
