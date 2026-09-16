"""Live integration tests against Google Cloud EU services."""

from __future__ import annotations

import os
import uuid
import wave
from pathlib import Path

import pytest
from dotenv import load_dotenv
from google.cloud import storage  # type: ignore[attr-defined]

from tts_audio_conversation.logic.auth import get_credentials_and_project
from tts_audio_conversation.logic.client import eu_tts_client, synthesize_script
from tts_audio_conversation.logic.models import (
    ConversationMetadata,
    ConversationScript,
    DialogueTurn,
    VoiceConfig,
)
from tts_audio_conversation.logic.storage import delete_file, download_file

load_dotenv()


def _delete_gcs_blob(gcs_client: storage.Client, gcs_uri: str) -> None:
    """Delete the test blob unless KEEP_TEST_ARTIFACTS=true."""
    if os.environ.get("KEEP_TEST_ARTIFACTS", "").strip().lower() == "true":
        return
    try:
        delete_file(gcs_client, gcs_uri)
    except Exception:
        pass


def _require_live_env() -> tuple[str, str]:
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    gcs_uri = os.environ.get("AUDIO_CONVERSATION_TEST_GCS_URI")
    if not project_id or not gcs_uri:
        pytest.skip(
            "GOOGLE_CLOUD_PROJECT and AUDIO_CONVERSATION_TEST_GCS_URI must be set "
            "for integration tests"
        )
    return project_id, gcs_uri


@pytest.mark.integration
def test_live_cloud_tts_synthesis(tmp_path: Path) -> None:
    """Live EU smoke test: batched synthesize_speech writes a non-empty WAV."""
    _project_id, gcs_uri = _require_live_env()
    credentials, resolved_proj = get_credentials_and_project(_project_id)
    tts_client = eu_tts_client(credentials)
    gcs_client = storage.Client(project=resolved_proj, credentials=credentials)

    script = ConversationScript(
        metadata=ConversationMetadata(
            title="Integration Test Podcast",
            language_code="en-US",
        ),
        voices={
            "host": VoiceConfig(name="en-US-Chirp3-HD-Fenrir", language_code="en-US"),
        },
        turns=[
            DialogueTurn(speaker="host", text="This is an EU endpoint live integration test."),
            DialogueTurn(speaker="host", text="Testing Chirp 3 HD voices in Google Cloud EU."),
        ],
    )

    unique_suffix = uuid.uuid4().hex[:8]
    base_prefix = gcs_uri.rsplit("/", 1)[0]
    output_uri = f"{base_prefix}/live_test_{unique_suffix}.wav"
    local_path = tmp_path / f"live_test_{unique_suffix}.wav"

    try:
        synthesize_script(
            tts_client,
            script,
            local_path,
            output_gcs_uri=output_uri,
            gcs_client=gcs_client,
        )
        assert local_path.exists()
        assert local_path.stat().st_size > 0
        with wave.open(str(local_path), "rb") as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getnframes() > 0
    finally:
        _delete_gcs_blob(gcs_client, output_uri)


@pytest.mark.integration
@pytest.mark.slow
def test_long_running_unicorn_fairytale_podcast(tmp_path: Path) -> None:
    """Live ~15-minute single-speaker fairytale via batched multi_speaker_markup."""
    _project_id, gcs_uri_env = _require_live_env()

    repo_root = Path(__file__).resolve().parents[2]
    template_path = repo_root / "templates" / "unicorn_fairytale.yaml"
    assert template_path.exists(), f"Static template not found at {template_path}"

    script = ConversationScript.from_path(template_path)
    assert len(script.voices) == 1, f"Expected 1 speaker, found {len(script.voices)}"
    assert "narrator" in script.voices
    total_words = sum(len(turn.text.split()) for turn in script.turns)
    assert 2000 <= total_words <= 2500, f"Expected ~2,150 words, got {total_words}"

    unique_suffix = uuid.uuid4().hex[:8]
    base_prefix = gcs_uri_env.rsplit("/", 1)[0]
    dest_gcs_uri = f"{base_prefix}/unicorn_fairytale_{unique_suffix}.wav"

    credentials, resolved_proj = get_credentials_and_project(_project_id)
    tts_client = eu_tts_client(credentials)
    gcs_client = storage.Client(project=resolved_proj, credentials=credentials)

    local_output_path = tmp_path / f"unicorn_fairytale_{unique_suffix}.wav"
    downloaded_path = tmp_path / f"downloaded_unicorn_{unique_suffix}.wav"

    try:
        synthesize_script(
            tts_client,
            script,
            local_output_path,
            output_gcs_uri=dest_gcs_uri,
            gcs_client=gcs_client,
        )
        assert local_output_path.exists()
        assert local_output_path.stat().st_size > 0

        download_file(gcs_client, dest_gcs_uri, downloaded_path)
        assert downloaded_path.exists()
        assert downloaded_path.stat().st_size > 0

        with wave.open(str(downloaded_path), "rb") as wav_file:
            duration_secs = wav_file.getnframes() / wav_file.getframerate()
            assert wav_file.getnchannels() == 1
            assert wav_file.getframerate() == script.metadata.sample_rate_hertz
            assert duration_secs > 300, f"Expected audio duration > 300s, got {duration_secs:.1f}s"
    finally:
        _delete_gcs_blob(gcs_client, dest_gcs_uri)


@pytest.mark.integration
@pytest.mark.slow
def test_live_two_speaker_german_ai_podcast(tmp_path: Path) -> None:
    """Live ~15-minute 2-speaker German AI podcast via batched multi_speaker_markup."""
    _project_id, gcs_uri_env = _require_live_env()

    repo_root = Path(__file__).resolve().parents[2]
    template_path = repo_root / "templates" / "ai_software_engineering_de.yaml"
    assert template_path.exists(), f"Static template not found at {template_path}"

    script = ConversationScript.from_path(template_path)
    assert len(script.voices) == 2, f"Expected 2 speakers, found {len(script.voices)}"
    assert "moderatorin" in script.voices
    assert "experte" in script.voices
    assert "Aoede" in script.voices["moderatorin"].name
    assert "Fenrir" in script.voices["experte"].name
    total_words = sum(len(turn.text.split()) for turn in script.turns)
    assert 2000 <= total_words <= 2500, f"Expected ~2,150 words, got {total_words}"

    unique_suffix = uuid.uuid4().hex[:8]
    base_prefix = gcs_uri_env.rsplit("/", 1)[0]
    dest_gcs_uri = f"{base_prefix}/ai_software_engineering_{unique_suffix}.wav"

    credentials, resolved_proj = get_credentials_and_project(_project_id)
    tts_client = eu_tts_client(credentials)
    gcs_client = storage.Client(project=resolved_proj, credentials=credentials)

    local_output_path = tmp_path / f"ai_software_engineering_{unique_suffix}.wav"
    downloaded_path = tmp_path / f"downloaded_ai_{unique_suffix}.wav"

    try:
        synthesize_script(
            tts_client,
            script,
            local_output_path,
            output_gcs_uri=dest_gcs_uri,
            gcs_client=gcs_client,
        )
        assert local_output_path.exists()
        assert local_output_path.stat().st_size > 0

        download_file(gcs_client, dest_gcs_uri, downloaded_path)
        assert downloaded_path.exists()
        assert downloaded_path.stat().st_size > 0

        with wave.open(str(downloaded_path), "rb") as wav_file:
            duration_secs = wav_file.getnframes() / wav_file.getframerate()
            assert wav_file.getnchannels() == 1
            assert wav_file.getframerate() == script.metadata.sample_rate_hertz
            assert duration_secs > 300, f"Expected audio duration > 300s, got {duration_secs:.1f}s"
    finally:
        _delete_gcs_blob(gcs_client, dest_gcs_uri)
