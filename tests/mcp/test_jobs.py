"""Unit tests for JobManager internals."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

import pytest
from opentelemetry import context as otel_context

from tests.mcp.helpers import instant_synth, job_manager, mock_handles
from tts_podcast_creator.logic.models import PodcastScript
from tts_podcast_creator.mcp.jobs import JobRecord, JobStatus, utc_now


def test_handles_are_memoized() -> None:
    """CloudHandles are created once and reused across persist/preflight/read."""
    calls = {"n": 0}

    def counting_factory() -> Any:
        calls["n"] += 1
        return mock_handles()

    manager, _gcs = job_manager(instant_synth)
    manager._clients_factory = counting_factory
    first = manager._handles()
    second = manager._handles()
    assert first is second
    assert calls["n"] == 1


def test_persist_record_propagates_upload_failure() -> None:
    """A failed status.json upload is not treated as success."""
    manager, _gcs = job_manager(instant_synth)

    def boom(_client: Any, _uri: str, _data: bytes, _content_type: str) -> None:
        raise RuntimeError("gcs down")

    manager._upload_bytes = boom
    record = JobRecord(
        job_id="job-1",
        status=JobStatus.queued,
        audio_uri="gs://test-eu-bucket/podcasts/job-1/audio.wav",
        status_uri="gs://test-eu-bucket/podcasts/job-1/status.json",
        updated_at=utc_now(),
    )
    with pytest.raises(RuntimeError, match="gcs down"):
        manager._persist_record(record)


def test_mark_does_not_resume_failed_job() -> None:
    """Failed jobs stay failed when a worker tries to mark them running."""
    manager, _gcs = job_manager(instant_synth)
    job_id = "failed-job"
    record = JobRecord(
        job_id=job_id,
        status=JobStatus.failed,
        audio_uri="gs://test-eu-bucket/podcasts/failed-job/audio.wav",
        status_uri="gs://test-eu-bucket/podcasts/failed-job/status.json",
        error="stale",
        updated_at=utc_now(),
    )
    with manager._lock:
        manager._jobs[job_id] = record
        manager._cancel_events[job_id] = threading.Event()
    marked = manager._mark(job_id, JobStatus.running, progress="starting")
    assert marked.status == JobStatus.failed
    assert manager._jobs[job_id].status == JobStatus.failed


def test_work_sync_does_not_succeed_failed_job(sample_script_yaml: str) -> None:
    """A stale-failed job is not reported as succeeded after TTS finishes."""
    manager, _gcs = job_manager(instant_synth)
    script = PodcastScript.from_payload(sample_script_yaml)
    job_id = "stale-failed"
    record = JobRecord(
        job_id=job_id,
        status=JobStatus.failed,
        audio_uri="gs://test-eu-bucket/podcasts/stale-failed/audio.wav",
        status_uri="gs://test-eu-bucket/podcasts/stale-failed/status.json",
        error="stale",
        updated_at=utc_now(),
    )
    with manager._lock:
        manager._jobs[job_id] = record
        manager._cancel_events[job_id] = threading.Event()
    manager._work_sync(job_id, script, None, otel_context.get_current())
    assert manager._jobs[job_id].status == JobStatus.failed


async def test_start_rolls_back_memory_when_queued_persist_fails(sample_script_yaml: str) -> None:
    """A failed initial status.json write must not leave a queued job in memory."""
    manager, _gcs = job_manager(instant_synth)

    def boom(_client: Any, _uri: str, _data: bytes, _content_type: str) -> None:
        raise RuntimeError("gcs down")

    manager._upload_bytes = boom
    with pytest.raises(RuntimeError, match="gcs down"):
        await manager.start(sample_script_yaml)
    assert manager._jobs == {}
    assert manager._cancel_events == {}
    assert manager._tasks == {}


def test_maybe_fail_stale_keeps_fresh_in_memory_record() -> None:
    """A stale snapshot must not fail a job that has since heartbeated."""
    manager, gcs = job_manager(instant_synth)
    job_id = "live-job"
    prefix = manager.settings.job_prefix_uri(job_id)
    stale = JobRecord(
        job_id=job_id,
        status=JobStatus.running,
        audio_uri=f"{prefix}/audio.wav",
        status_uri=f"{prefix}/status.json",
        progress="batch 1/9",
        updated_at=utc_now() - timedelta(hours=2),
    )
    fresh = stale.model_copy(update={"updated_at": utc_now(), "progress": "batch 2/9"})
    with manager._lock:
        manager._jobs[job_id] = fresh
        manager._cancel_events[job_id] = threading.Event()
    result = manager._maybe_fail_stale(stale, True)
    assert result.status == JobStatus.running
    assert result.progress == "batch 2/9"
    assert manager._jobs[job_id].status == JobStatus.running
    assert f"{prefix}/status.json" not in gcs.objects


def test_maybe_fail_stale_preserves_terminal_in_memory_record() -> None:
    """A stale running snapshot must not overwrite a succeeded in-memory job."""
    manager, gcs = job_manager(instant_synth)
    job_id = "done-job"
    prefix = manager.settings.job_prefix_uri(job_id)
    succeeded = JobRecord(
        job_id=job_id,
        status=JobStatus.succeeded,
        audio_uri=f"{prefix}/audio.wav",
        status_uri=f"{prefix}/status.json",
        updated_at=utc_now(),
    )
    stale_running = succeeded.model_copy(
        update={"status": JobStatus.running, "updated_at": utc_now() - timedelta(hours=2)}
    )
    with manager._lock:
        manager._jobs[job_id] = succeeded
        manager._cancel_events[job_id] = threading.Event()
    result = manager._maybe_fail_stale(stale_running, True)
    assert result.status == JobStatus.succeeded
    assert manager._jobs[job_id].status == JobStatus.succeeded
    assert f"{prefix}/status.json" not in gcs.objects


def test_maybe_fail_stale_fails_when_current_is_still_stale() -> None:
    """Queued/running jobs that are still stale become failed and are persisted."""
    manager, gcs = job_manager(instant_synth)
    job_id = "stale-mem"
    prefix = manager.settings.job_prefix_uri(job_id)
    stale = JobRecord(
        job_id=job_id,
        status=JobStatus.queued,
        audio_uri=f"{prefix}/audio.wav",
        status_uri=f"{prefix}/status.json",
        updated_at=utc_now() - timedelta(hours=2),
    )
    with manager._lock:
        manager._jobs[job_id] = stale
        manager._cancel_events[job_id] = threading.Event()
    result = manager._maybe_fail_stale(stale, True)
    assert result.status == JobStatus.failed
    assert manager._jobs[job_id].status == JobStatus.failed
    stored = json.loads(gcs.objects[f"{prefix}/status.json"].decode("utf-8"))
    assert stored["status"] == "failed"


async def test_status_and_cancel_stay_responsive_when_worker_pool_is_busy() -> None:
    """Status and cancel must not wait on the TTS worker executor."""
    manager, _gcs = job_manager(instant_synth)
    job_id = "ctl-job"
    prefix = manager.settings.job_prefix_uri(job_id)
    record = JobRecord(
        job_id=job_id,
        status=JobStatus.queued,
        audio_uri=f"{prefix}/audio.wav",
        status_uri=f"{prefix}/status.json",
        updated_at=utc_now(),
    )
    with manager._lock:
        manager._jobs[job_id] = record
        manager._cancel_events[job_id] = threading.Event()

    manager._executor.shutdown(wait=False)
    blocked = threading.Event()
    release = threading.Event()
    manager._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="podcast-tts")

    def occupy() -> None:
        blocked.set()
        assert release.wait(timeout=5)

    occupy_future = asyncio.get_running_loop().run_in_executor(manager._executor, occupy)
    try:
        assert await asyncio.to_thread(blocked.wait, 2)
        t0 = time.perf_counter()
        status = await manager.get_status(job_id)
        assert time.perf_counter() - t0 < 0.4
        assert status.status == JobStatus.queued
        t0 = time.perf_counter()
        cancelled = await manager.cancel(job_id)
        assert time.perf_counter() - t0 < 0.4
        assert cancelled.status == JobStatus.cancelled
    finally:
        release.set()
        await occupy_future
        manager._executor.shutdown(wait=False)
