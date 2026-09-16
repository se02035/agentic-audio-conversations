"""Unit tests for the ``cancel_conversation`` MCP tool."""

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
from tts_audio_conversation.logic.exceptions import SynthesisCancelled
from tts_audio_conversation.mcp.jobs import JobStatus


async def test_cancel_conversation_unknown_job_id() -> None:
    """Unknown ids return a not-found tool error."""
    mcp, _manager, _gcs = mcp_app(instant_synth)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="Unknown job_id"):
            await client.call_tool("cancel_conversation", {"job_id": "missing"})


async def test_cancel_conversation_is_noop_when_already_succeeded(sample_script_yaml: str) -> None:
    """Cancel after success leaves the job succeeded."""
    mcp, _manager, _gcs = mcp_app(instant_synth)
    async with Client(mcp) as client:
        started = tool_data(
            await client.call_tool("start_conversation", {"script": sample_script_yaml})
        )
        deadline = time.monotonic() + 5
        status = started
        while time.monotonic() < deadline:
            status = tool_data(
                await client.call_tool("get_conversation_status", {"job_id": started["job_id"]})
            )
            if status["status"] == JobStatus.succeeded:
                break
            await asyncio.sleep(0.05)
        assert status["status"] == JobStatus.succeeded
        cancelled = tool_data(
            await client.call_tool("cancel_conversation", {"job_id": started["job_id"]})
        )
    assert cancelled["status"] == JobStatus.succeeded


async def test_cancel_conversation_stops_further_batches(sample_script_yaml: str) -> None:
    """cancel_conversation returns immediately; later mocked batches are skipped."""
    batches: list[int] = []
    first_batch = threading.Event()

    def batched_synth(_client: Any, _script: Any, output_path: Path, **kwargs: Any) -> Path:
        should_cancel = kwargs.get("should_cancel")
        on_progress = kwargs.get("on_progress")
        for idx in range(1, 6):
            if should_cancel is not None and should_cancel():
                raise SynthesisCancelled()
            if on_progress is not None:
                on_progress(idx, 5)
            batches.append(idx)
            if idx == 1:
                first_batch.set()
            time.sleep(0.12)
        return write_fake_wav(output_path)

    mcp, manager, gcs = mcp_app(batched_synth)
    async with Client(mcp) as client:
        started = tool_data(
            await client.call_tool("start_conversation", {"script": sample_script_yaml})
        )
        worker = manager._tasks[started["job_id"]]
        assert await asyncio.to_thread(first_batch.wait, 2)
        t0 = time.perf_counter()
        cancelled = tool_data(
            await client.call_tool("cancel_conversation", {"job_id": started["job_id"]})
        )
        assert time.perf_counter() - t0 < 0.4
        assert cancelled["status"] == JobStatus.cancelled
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = tool_data(
                await client.call_tool("get_conversation_status", {"job_id": started["job_id"]})
            )
            if status["status"] == JobStatus.cancelled:
                break
            await asyncio.sleep(0.05)
        await worker
    assert 5 not in batches
    assert started["audio_uri"] not in gcs.objects


async def test_cancel_conversation_gcs_only_job_does_not_start_worker(
    sample_script_yaml: str,
) -> None:
    """After restart, cancel writes cancelled to GCS and does not synthesize."""
    mcp, manager, gcs = mcp_app(instant_synth)
    async with Client(mcp) as client:
        started = tool_data(
            await client.call_tool("start_conversation", {"script": sample_script_yaml})
        )
        job_id = started["job_id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = tool_data(
                await client.call_tool("get_conversation_status", {"job_id": job_id})
            )
            if status["status"] == JobStatus.succeeded:
                break
            await asyncio.sleep(0.05)
        raw = gcs.objects[started["status_uri"]]
        queuedish = json.loads(raw.decode("utf-8"))
        queuedish["status"] = "running"
        queuedish["updated_at"] = "2099-01-01T00:00:00+00:00"
        gcs.objects[started["status_uri"]] = json.dumps(queuedish).encode("utf-8")
        with manager._lock:
            manager._jobs.pop(job_id, None)
            manager._cancel_events.pop(job_id, None)
        cancelled = tool_data(await client.call_tool("cancel_conversation", {"job_id": job_id}))
    assert cancelled["status"] == JobStatus.cancelled
    assert job_id not in manager._jobs
    stored = json.loads(gcs.objects[started["status_uri"]].decode("utf-8"))
    assert stored["status"] == "cancelled"
