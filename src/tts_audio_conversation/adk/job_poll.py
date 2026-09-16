"""Poll helpers for the LRO resume plugin (not used inside the start tool)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from tts_audio_conversation.logic.jobs.models import TERMINAL_STATUSES, JobStatus

GetStatus = Callable[[str], Awaitable[Mapping[str, Any]]]
SleepFn = Callable[[float], Awaitable[None]]

TERMINAL_STATUS_VALUES = frozenset(status.value for status in TERMINAL_STATUSES)


class JobPollTimeout(TimeoutError):
    """Raised when a job does not reach a terminal status before ``timeout_sec``."""

    def __init__(self, job_id: str, timeout_sec: float) -> None:
        """Record the job that did not finish in time."""
        self.job_id = job_id
        self.timeout_sec = timeout_sec
        super().__init__(f"Job '{job_id}' did not reach a terminal status within {timeout_sec:g}s.")


def is_terminal_status(status: object) -> bool:
    """Return True for succeeded, failed, or cancelled (str or ``JobStatus``)."""
    if isinstance(status, JobStatus):
        return status in TERMINAL_STATUSES
    return str(status).lower() in TERMINAL_STATUS_VALUES


def terminal_function_response(
    job: Mapping[str, Any],
    *,
    audio_artifact: str | None = None,
    download_error: str | None = None,
    poll_error: str | None = None,
) -> dict[str, Any]:
    """Build the JSON payload sent as the matching LRO ``FunctionResponse``."""
    payload: dict[str, Any] = {
        "job_id": str(job.get("job_id") or ""),
        "status": str(job.get("status") or "failed"),
        "script_uri": str(job.get("script_uri") or ""),
        "audio_uri": str(job.get("audio_uri") or ""),
        "error": job.get("error"),
    }
    if audio_artifact:
        payload["audio_artifact"] = audio_artifact
    if download_error:
        payload["download_error"] = download_error
    if poll_error:
        payload["error"] = poll_error
        if payload["status"] not in TERMINAL_STATUS_VALUES:
            payload["status"] = JobStatus.failed.value
    return payload


async def wait_for_terminal_job(
    get_status: GetStatus,
    job_id: str,
    *,
    interval_sec: float,
    timeout_sec: float,
    sleep: SleepFn | None = None,
) -> dict[str, Any]:
    """Poll ``get_status`` until the job is terminal or ``timeout_sec`` elapses.

    Args:
        get_status: Async callable returning a job snapshot dict.
        job_id: MCP job id from ``start_conversation``.
        interval_sec: Delay between polls.
        timeout_sec: Maximum wait (plugin-side; not an ADK tool timeout).
        sleep: Injected sleeper for tests (defaults to ``asyncio.sleep``).

    Returns:
        The first terminal job snapshot as a plain dict.

    Raises:
        JobPollTimeout: When the deadline is exceeded while still queued/running.
    """
    sleeper = sleep or asyncio.sleep
    snapshot = dict(await get_status(job_id))
    if is_terminal_status(snapshot.get("status")):
        return snapshot
    elapsed = 0.0
    while elapsed < timeout_sec:
        await sleeper(interval_sec)
        elapsed += interval_sec
        snapshot = dict(await get_status(job_id))
        if is_terminal_status(snapshot.get("status")):
            return snapshot
    raise JobPollTimeout(job_id, timeout_sec)
