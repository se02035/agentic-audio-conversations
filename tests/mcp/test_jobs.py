"""Unit tests for JobManager internals."""

from __future__ import annotations

import threading
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
