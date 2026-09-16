"""Live library-facade integration tests (AudioConversationService)."""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv

from tts_audio_conversation.logic.exceptions import JobNotFound, ScriptPayloadError
from tts_audio_conversation.logic.jobs.models import TERMINAL_STATUSES, JobStatus
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
        audio_conversation_gcs_prefix=f"conversation/lib_it_{uuid.uuid4().hex[:8]}",
        otel_traces_exporter="none",
    )


def _smoke_script() -> ConversationScript:
    return ConversationScript(
        metadata=ConversationMetadata(
            title="Library API Smoke",
            language_code="en-US",
        ),
        voices={
            "host": VoiceConfig(name="en-US-Chirp3-HD-Fenrir", language_code="en-US"),
        },
        turns=[
            DialogueTurn(speaker="host", text="Library facade upload and create_audio smoke."),
            DialogueTurn(speaker="host", text="EU Chirp 3 HD via AudioConversationService."),
        ],
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
def test_library_upload_validate_create_download(tmp_path: Path) -> None:
    """Happy path: upload → validate → create_audio → poll → download."""
    settings = _require_live_settings()
    service = create_audio_conversation_service_from_adc(settings)
    uris: list[str] = []
    try:
        uploaded = service.upload_script(_smoke_script().to_yaml())
        uris.append(uploaded.script_uri)
        assert "/scripts/" in uploaded.script_uri
        assert uploaded.script_uri.endswith("/script.yaml")

        valid = service.validate_script(uploaded.script_uri)
        assert valid.valid is True
        assert valid.script_uri == uploaded.script_uri

        job = asyncio.run(service.create_audio(uploaded.script_uri))
        uris.extend([job.audio_uri, job.status_uri])
        assert job.status in {JobStatus.queued, JobStatus.running}
        assert "/jobs/" in job.audio_uri
        assert job.audio_uri.endswith("/output/audio.wav")

        async def _poll() -> None:
            deadline_status = None
            for _ in range(240):
                deadline_status = await service.get_job(job.job_id)
                if deadline_status.status in TERMINAL_STATUSES:
                    break
                await asyncio.sleep(0.5)
            assert deadline_status is not None
            assert deadline_status.status == JobStatus.succeeded, deadline_status.error

        asyncio.run(_poll())
        dest = tmp_path / "lib_smoke.wav"
        downloaded = service.download(job.audio_uri, dest)
        assert downloaded.local_path.exists()
        assert dest.stat().st_size > 0
    finally:
        _cleanup(service, uris)


@pytest.mark.integration
def test_library_validate_missing_uri_is_soft() -> None:
    """Missing gs:// object returns valid=False (no raise)."""
    settings = _require_live_settings()
    service = create_audio_conversation_service_from_adc(settings)
    missing = (
        f"gs://{settings.audio_conversation_gcs_staging_bucket}/"
        f"{settings.audio_conversation_gcs_prefix}/scripts/missing/script.yaml"
    )
    result = service.validate_script(missing)
    assert result.valid is False
    assert result.error


@pytest.mark.integration
def test_library_upload_rejects_bad_payload() -> None:
    """Invalid inline YAML raises ScriptPayloadError."""
    settings = _require_live_settings()
    service = create_audio_conversation_service_from_adc(settings)
    with pytest.raises(ScriptPayloadError):
        service.upload_script("not: [yaml")


@pytest.mark.integration
def test_library_create_audio_missing_object_raises() -> None:
    """create_audio on a nonexistent script URI fails before enqueue."""
    settings = _require_live_settings()
    service = create_audio_conversation_service_from_adc(settings)
    missing = (
        f"gs://{settings.audio_conversation_gcs_staging_bucket}/"
        f"{settings.audio_conversation_gcs_prefix}/scripts/nope/script.yaml"
    )
    with pytest.raises(ScriptPayloadError):
        asyncio.run(service.create_audio(missing))


@pytest.mark.integration
def test_library_get_job_unknown_raises() -> None:
    """Unknown job_id raises JobNotFound."""
    settings = _require_live_settings()
    service = create_audio_conversation_service_from_adc(settings)
    with pytest.raises(JobNotFound):
        asyncio.run(service.get_job("00000000-0000-0000-0000-000000000000"))


@pytest.mark.integration
def test_library_missing_bucket_config_raises() -> None:
    """upload_script without staging bucket raises configuration error."""
    with pytest.raises(ValueError, match="AUDIO_CONVERSATION_GCS_STAGING_BUCKET"):
        create_audio_conversation_service_from_adc(
            Settings(
                google_cloud_project=os.environ.get("GOOGLE_CLOUD_PROJECT") or "x",
                audio_conversation_gcs_staging_bucket=None,
                otel_traces_exporter="none",
            )
        ).upload_script(_smoke_script().to_yaml())
