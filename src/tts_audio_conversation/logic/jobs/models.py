"""Job status and public job snapshots."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    """Lifecycle states for an audio-creation job."""

    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


TERMINAL_STATUSES = frozenset({JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled})

ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.queued: frozenset({JobStatus.running, JobStatus.cancelled, JobStatus.failed}),
    JobStatus.running: frozenset({JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled}),
    JobStatus.succeeded: frozenset(),
    JobStatus.failed: frozenset(),
    JobStatus.cancelled: frozenset(),
}


class JobRecord(BaseModel):
    """Public job snapshot returned by APIs and stored as ``status.json``."""

    job_id: str = Field(description="UUID for this job; pass to get_job / cancel_job.")
    status: JobStatus = Field(description="queued | running | succeeded | failed | cancelled.")
    script_uri: str = Field(
        default="",
        description=(
            "gs:// URI of the script used as TTS input. Empty only for legacy or "
            "incomplete status.json rows; create_audio always starts from a GCS URI."
        ),
    )
    audio_uri: str = Field(description="gs:// URI of the output WAV path.")
    status_uri: str = Field(description="gs:// URI of this status.json object.")
    error: str | None = Field(default=None, description="Failure reason when failed.")
    updated_at: datetime | None = Field(
        default=None,
        description="UTC time of last status transition.",
    )
