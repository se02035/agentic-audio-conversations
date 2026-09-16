"""Unit tests for concurrent MCP jobs and asyncio task scheduling."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

from fastmcp import Client

from tests.unit.mcp.helpers import (
    mcp_app,
    mcp_settings,
    tool_data,
    upload_and_start,
    write_fake_wav,
)
from tts_audio_conversation.logic.jobs.models import JobStatus


async def test_two_jobs_run_as_separate_asyncio_tasks_with_independent_status(
    sample_script_yaml: str,
) -> None:
    """Two start_conversation calls overlap in the thread pool and poll independently."""
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0
    job_progress: dict[str, list[str]] = {}

    def overlapping_synth(_client: Any, _script: Any, output_path: Path, **kwargs: Any) -> Path:
        nonlocal in_flight, max_in_flight
        on_progress = kwargs.get("on_progress")
        with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        barrier.wait(timeout=3)
        if on_progress is not None:
            on_progress(1, 2)
        time.sleep(0.2)
        if on_progress is not None:
            on_progress(2, 2)
        with lock:
            in_flight -= 1
        return write_fake_wav(output_path)

    mcp, service, gcs = mcp_app(overlapping_synth)
    manager = service._jobs
    async with Client(mcp) as client:
        t0 = time.perf_counter()
        first = await upload_and_start(client, sample_script_yaml)
        second = await upload_and_start(client, sample_script_yaml)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.4
        assert first["job_id"] != second["job_id"]
        live_tasks = [task for task in manager._tasks.values() if not task.done()]
        assert len(live_tasks) == 2

        saw_both_running = False
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            s1 = tool_data(
                await client.call_tool("get_conversation_status", {"job_id": first["job_id"]})
            )
            s2 = tool_data(
                await client.call_tool("get_conversation_status", {"job_id": second["job_id"]})
            )
            job_progress.setdefault(first["job_id"], []).append(s1["status"])
            job_progress.setdefault(second["job_id"], []).append(s2["status"])
            if s1["status"] == JobStatus.running and s2["status"] == JobStatus.running:
                saw_both_running = True
            if s1["status"] == JobStatus.succeeded and s2["status"] == JobStatus.succeeded:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("overlapping jobs did not both succeed")

    assert max_in_flight == 2
    assert saw_both_running
    assert JobStatus.succeeded in job_progress[first["job_id"]]
    assert JobStatus.succeeded in job_progress[second["job_id"]]
    assert first["audio_uri"] in gcs.objects
    assert second["audio_uri"] in gcs.objects
    assert first["audio_uri"] != second["audio_uri"]


async def test_third_job_queued_when_semaphore_full(sample_script_yaml: str) -> None:
    """start_conversation returns immediately when TTS cap is full; extra stays queued."""
    release = threading.Event()
    entered = threading.Event()

    def gated_synth(_client: Any, _script: Any, output_path: Path, **_kwargs: Any) -> Path:
        entered.set()
        assert release.wait(timeout=5)
        return write_fake_wav(output_path)

    mcp, service, _gcs = mcp_app(
        gated_synth,
        settings=mcp_settings(audio_conversation_max_concurrent_jobs=1),
    )
    manager = service._jobs
    async with Client(mcp) as client:
        first = await upload_and_start(client, sample_script_yaml)
        assert await asyncio.to_thread(entered.wait, 2)
        t0 = time.perf_counter()
        second = await upload_and_start(client, sample_script_yaml)
        assert time.perf_counter() - t0 < 0.4
        status = tool_data(
            await client.call_tool("get_conversation_status", {"job_id": second["job_id"]})
        )
        assert status["status"] == JobStatus.queued
        assert first["job_id"] != second["job_id"]
        assert len(manager._tasks) == 2
        release.set()
