"""Unit tests for ConversationPipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from tts_audio_conversation.logic.exceptions import SynthesisCancelled
from tts_audio_conversation.logic.models import ConversationScript
from tts_audio_conversation.logic.pipeline import ConversationPipeline
from tts_audio_conversation.logic.voices import VoiceCatalogService


@pytest.fixture
def script(sample_script_dict: dict[str, Any]) -> ConversationScript:
    """Validated sample script."""
    return ConversationScript.model_validate(sample_script_dict)


def test_preflight_voices_does_not_synthesize(script: ConversationScript) -> None:
    """preflight_voices only hits the voice catalog."""
    tts = MagicMock()
    storage = MagicMock()
    voices = MagicMock(spec=VoiceCatalogService)
    pipeline = ConversationPipeline(tts=tts, storage=storage, voices=voices)

    pipeline.preflight_voices(script)

    voices.assert_in_catalog.assert_called_once_with(script)
    tts.synthesize.assert_not_called()
    storage.upload_file.assert_not_called()


def test_run_happy_path_upload_order(script: ConversationScript, tmp_path: Path) -> None:
    """run asserts voices, synthesizes, then uploads."""
    tts = MagicMock()
    storage = MagicMock()
    voices = MagicMock(spec=VoiceCatalogService)
    local = tmp_path / "out.wav"
    local.write_bytes(b"RIFF")
    tts.synthesize.return_value = local
    pipeline = ConversationPipeline(tts=tts, storage=storage, voices=voices)
    uri = "gs://bucket/conversation/jobs/j1/output/audio.wav"

    result = pipeline.run(script, output_path=local, output_gcs_uri=uri)

    voices.assert_in_catalog.assert_called_once_with(script)
    tts.synthesize.assert_called_once()
    storage.upload_file.assert_called_once_with(uri, local)
    assert result.local_path == local
    assert result.gcs_uri == uri


def test_run_cancel_mid_batch_no_upload(script: ConversationScript, tmp_path: Path) -> None:
    """SynthesisCancelled before upload leaves GCS untouched."""
    tts = MagicMock()
    storage = MagicMock()
    voices = MagicMock(spec=VoiceCatalogService)
    tts.synthesize.side_effect = SynthesisCancelled()
    pipeline = ConversationPipeline(tts=tts, storage=storage, voices=voices)

    with pytest.raises(SynthesisCancelled):
        pipeline.run(
            script,
            output_path=tmp_path / "out.wav",
            output_gcs_uri="gs://b/a.wav",
        )

    storage.upload_file.assert_not_called()


def test_run_cancel_post_upload_deletes_blob(script: ConversationScript, tmp_path: Path) -> None:
    """Cancel after upload deletes the uploaded object."""
    tts = MagicMock()
    storage = MagicMock()
    voices = MagicMock(spec=VoiceCatalogService)
    local = tmp_path / "out.wav"
    local.write_bytes(b"RIFF")
    tts.synthesize.return_value = local
    pipeline = ConversationPipeline(tts=tts, storage=storage, voices=voices)
    uri = "gs://bucket/a.wav"
    cancelled = {"n": 0}

    def should_cancel() -> bool:
        cancelled["n"] += 1
        return cancelled["n"] > 1

    with pytest.raises(SynthesisCancelled):
        pipeline.run(
            script,
            output_path=local,
            output_gcs_uri=uri,
            should_cancel=should_cancel,
        )

    storage.upload_file.assert_called_once_with(uri, local)
    storage.delete_file.assert_called_once_with(uri)
