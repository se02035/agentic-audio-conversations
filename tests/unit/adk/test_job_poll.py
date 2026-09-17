"""Unit tests for the plugin-side job poll helper (not the LRO start tool)."""

from __future__ import annotations

import asyncio

import pytest

from tts_audio_conversation.adk.job_poll import (
    JobPollTimeout,
    is_terminal_status,
    terminal_function_response,
    wait_for_terminal_job,
)
from tts_audio_conversation.logic.jobs.models import JobStatus


def test_is_terminal_status_accepts_enum_and_str() -> None:
    """Queued/running stay open; succeeded/failed/cancelled are terminal."""
    assert not is_terminal_status(JobStatus.queued)
    assert not is_terminal_status("running")
    assert is_terminal_status(JobStatus.succeeded)
    assert is_terminal_status("failed")
    assert is_terminal_status("cancelled")


def test_terminal_function_response_includes_artifact_and_errors() -> None:
    """Resume payload is JSON-friendly and carries optional download errors."""
    payload = terminal_function_response(
        {
            "job_id": "j1",
            "status": "succeeded",
            "script_uri": "gs://b/s.yaml",
            "audio_uri": "gs://b/a.wav",
            "error": None,
        },
        audio_artifact="audio_j1_en-US.wav",
        download_error=None,
    )
    assert payload["audio_artifact"] == "audio_j1_en-US.wav"
    assert payload["status"] == "succeeded"
    timed_out = terminal_function_response(
        {"job_id": "j2", "status": "running"},
        poll_error="deadline",
    )
    assert timed_out["status"] == "failed"
    assert timed_out["error"] == "deadline"


async def test_wait_for_terminal_job_returns_when_done() -> None:
    """Poller returns the first terminal snapshot and does not wait further."""
    snapshots = [
        {"job_id": "j1", "status": "queued"},
        {"job_id": "j1", "status": "running"},
        {"job_id": "j1", "status": "succeeded"},
    ]
    sleeps: list[float] = []

    async def get_status(_job_id: str) -> dict[str, str]:
        return snapshots.pop(0)

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    result = await wait_for_terminal_job(
        get_status,
        "j1",
        interval_sec=0.5,
        timeout_sec=10,
        sleep=fake_sleep,
    )
    assert result["status"] == "succeeded"
    assert sleeps == [0.5, 0.5]


def test_poll_error_on_already_terminal_keeps_status() -> None:
    """poll_error on a failed snapshot does not rewrite the status."""
    payload = terminal_function_response(
        {"job_id": "j", "status": "failed", "error": "tts"},
        poll_error="also timed out",
    )
    assert payload["status"] == "failed"
    assert payload["error"] == "also timed out"


async def test_wait_for_terminal_job_times_out() -> None:
    """Non-terminal snapshots eventually raise ``JobPollTimeout``."""

    async def get_status(_job_id: str) -> dict[str, str]:
        return {"job_id": "j1", "status": "running"}

    async def fake_sleep(_delay: float) -> None:
        return None

    with pytest.raises(JobPollTimeout):
        await wait_for_terminal_job(
            get_status,
            "j1",
            interval_sec=0.01,
            timeout_sec=0.0,
            sleep=fake_sleep,
        )


async def test_wait_for_terminal_job_enforces_wall_clock_deadline() -> None:
    """A hung ``get_status`` is aborted by ``asyncio.timeout``, not the interval counter."""

    async def hang(_job_id: str) -> dict[str, str]:
        await asyncio.Event().wait()
        return {"job_id": "j1", "status": "running"}

    with pytest.raises(JobPollTimeout) as exc_info:
        await wait_for_terminal_job(
            hang,
            "j1",
            interval_sec=60,
            timeout_sec=0.05,
        )
    assert exc_info.value.job_id == "j1"
    assert exc_info.value.timeout_sec == 0.05
