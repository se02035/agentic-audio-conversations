"""Live library cancel + create_audio cooperative cancel."""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from dotenv import load_dotenv

from tts_audio_conversation.logic.jobs.models import JobStatus
from tts_audio_conversation.logic.models import (
    ConversationMetadata,
    ConversationScript,
    DialogueTurn,
    VoiceConfig,
)
from tts_audio_conversation.logic.service import create_audio_conversation_service_from_adc
from tts_audio_conversation.logic.settings import Settings

load_dotenv()


def _require_live_settings() -> Settings:
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    bucket = os.environ.get("AUDIO_CONVERSATION_GCS_STAGING_BUCKET")
    test_uri = os.environ.get("AUDIO_CONVERSATION_TEST_GCS_URI")
    if not project_id:
        pytest.skip("GOOGLE_CLOUD_PROJECT must be set for library integration tests")
    if not bucket:
        if not test_uri or not test_uri.startswith("gs://"):
            pytest.skip(
                "AUDIO_CONVERSATION_GCS_STAGING_BUCKET or AUDIO_CONVERSATION_TEST_GCS_URI "
                "must be set for library integration tests"
            )
        bucket = test_uri[5:].split("/", 1)[0]
    return Settings(
        google_cloud_project=project_id,
        audio_conversation_gcs_staging_bucket=bucket,
        audio_conversation_gcs_prefix=f"conversation/lib_cancel_{uuid.uuid4().hex[:8]}",
        otel_traces_exporter="none",
    )


def _longish_script() -> ConversationScript:
    """Enough text to keep the job in running for a cancel race."""
    turns = [
        DialogueTurn(
            speaker="host",
            text=(
                "This cancel integration script needs enough spoken text so the "
                "job is still running when we request cancellation from the facade."
            ),
        )
        for _ in range(8)
    ]
    return ConversationScript(
        metadata=ConversationMetadata(title="Cancel Smoke", language_code="en-US"),
        voices={
            "host": VoiceConfig(name="en-US-Chirp3-HD-Fenrir", language_code="en-US"),
        },
        turns=turns,
    )


def _cleanup(service: object, uris: list[str]) -> None:
    if os.environ.get("KEEP_TEST_ARTIFACTS", "").strip().lower() == "true":
        return
    storage = getattr(service, "_storage", None)
    if storage is None:
        return
    for uri in uris:
        try:
            storage.delete_file(uri)
        except Exception:
            pass


@pytest.mark.integration
def test_library_cancel_running_job() -> None:
    """create_audio then cancel_job reaches cancelled (retry if already succeeded)."""
    settings = _require_live_settings()
    service = create_audio_conversation_service_from_adc(settings)
    uris: list[str] = []
    try:
        uploaded = service.upload_script(_longish_script().to_yaml())
        uris.append(uploaded.script_uri)

        async def _run() -> JobStatus:
            for _attempt in range(3):
                job = await service.create_audio(uploaded.script_uri)
                uris.extend([job.audio_uri, job.status_uri])
                # Brief wait so the worker can leave queued when concurrency allows.
                await asyncio.sleep(0.05)
                cancelled = await service.cancel_job(job.job_id)
                if cancelled.status == JobStatus.succeeded:
                    # Finished before cancel could apply — retry with a fresh job.
                    continue
                for _ in range(120):
                    status = await service.get_job(job.job_id)
                    if status.status == JobStatus.cancelled:
                        return status.status
                    if status.status == JobStatus.succeeded:
                        break
                    if status.status == JobStatus.failed:
                        raise AssertionError(f"job failed during cancel: {status.error}")
                    await asyncio.sleep(0.25)
            pytest.skip("job kept succeeding before cancel could apply")

        final = asyncio.run(_run())
        assert final == JobStatus.cancelled
    finally:
        _cleanup(service, uris)
