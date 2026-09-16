"""Unit tests for JobManager internals."""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from tests.unit.mcp.helpers import instant_synth, job_manager, mock_handles
from tts_audio_conversation.logic.jobs.models import ALLOWED_TRANSITIONS
from tts_audio_conversation.logic.models import ConversationScript
from tts_audio_conversation.mcp.jobs import JobRecord, JobStatus, utc_now


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
    prefix = manager.settings.job_prefix_uri("job-1")
    record = JobRecord(
        job_id="job-1",
        status=JobStatus.queued,
        audio_uri=f"{prefix}/output/audio.wav",
        status_uri=f"{prefix}/status.json",
        updated_at=utc_now(),
    )
    with pytest.raises(RuntimeError, match="gcs down"):
        manager._persist_record(record)


def test_transition_does_not_resume_failed_job() -> None:
    """Failed jobs stay failed when a worker tries to mark them running."""
    manager, _gcs = job_manager(instant_synth)
    job_id = "failed-job"
    prefix = manager.settings.job_prefix_uri(job_id)
    record = JobRecord(
        job_id=job_id,
        status=JobStatus.failed,
        audio_uri=f"{prefix}/output/audio.wav",
        status_uri=f"{prefix}/status.json",
        error="boom",
        updated_at=utc_now(),
    )
    with manager._lock:
        manager._jobs[job_id] = record
        manager._cancel_events[job_id] = threading.Event()
    marked = manager._transition(job_id, JobStatus.running)
    assert marked.status == JobStatus.failed
    assert manager._jobs[job_id].status == JobStatus.failed
    assert JobStatus.running not in ALLOWED_TRANSITIONS[JobStatus.failed]


def test_work_sync_does_not_succeed_failed_job(sample_script_yaml: str) -> None:
    """A failed job is not reported as succeeded after TTS finishes."""
    manager, _gcs = job_manager(instant_synth)
    script = ConversationScript.from_payload(sample_script_yaml)
    job_id = "already-failed"
    prefix = manager.settings.job_prefix_uri(job_id)
    record = JobRecord(
        job_id=job_id,
        status=JobStatus.failed,
        audio_uri=f"{prefix}/output/audio.wav",
        status_uri=f"{prefix}/status.json",
        error="boom",
        updated_at=utc_now(),
    )
    with manager._lock:
        manager._jobs[job_id] = record
        manager._cancel_events[job_id] = threading.Event()
    manager._work_sync(job_id, script)
    assert manager._jobs[job_id].status == JobStatus.failed


async def test_start_rolls_back_memory_when_queued_persist_fails(sample_script_yaml: str) -> None:
    """A failed initial status.json write must not leave a queued job in memory."""
    manager, gcs = job_manager(instant_synth)
    uri = "gs://test-eu-bucket/conversation/scripts/x/script.yaml"
    gcs.objects[uri] = sample_script_yaml.encode()

    def boom(_client: Any, _uri: str, _data: bytes, _content_type: str) -> None:
        raise RuntimeError("gcs down")

    manager._upload_bytes = boom
    with pytest.raises(RuntimeError, match="gcs down"):
        await manager.start(uri)
    assert manager._jobs == {}
    assert manager._cancel_events == {}
    assert manager._tasks == {}


def test_transition_rejects_cancelled_to_succeeded() -> None:
    """Cancelled must not become succeeded (illegal edge)."""
    manager, _gcs = job_manager(instant_synth)
    job_id = "cancel-race"
    prefix = manager.settings.job_prefix_uri(job_id)
    record = JobRecord(
        job_id=job_id,
        status=JobStatus.cancelled,
        audio_uri=f"{prefix}/output/audio.wav",
        status_uri=f"{prefix}/status.json",
        updated_at=utc_now(),
    )
    with manager._lock:
        manager._jobs[job_id] = record
        manager._cancel_events[job_id] = threading.Event()
    result = manager._transition(job_id, JobStatus.succeeded)
    assert result.status == JobStatus.cancelled


async def test_status_and_cancel_stay_responsive_when_worker_pool_is_busy() -> None:
    """Status and cancel must not wait on the TTS worker executor."""
    manager, _gcs = job_manager(instant_synth)
    job_id = "ctl-job"
    prefix = manager.settings.job_prefix_uri(job_id)
    record = JobRecord(
        job_id=job_id,
        status=JobStatus.queued,
        audio_uri=f"{prefix}/output/audio.wav",
        status_uri=f"{prefix}/status.json",
        updated_at=utc_now(),
    )
    with manager._lock:
        manager._jobs[job_id] = record
        manager._cancel_events[job_id] = threading.Event()

    manager._executor.shutdown(wait=False)
    blocked = threading.Event()
    release = threading.Event()
    manager._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="conversation-tts")

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
