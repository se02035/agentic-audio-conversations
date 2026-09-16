"""Unit tests for TtsSynthesisService (no GCS)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from tts_audio_conversation.logic.models import ConversationScript
from tts_audio_conversation.logic.tts import TtsSynthesisService


def test_tts_synthesis_service_does_not_call_gcs(
    sample_script_dict: dict[str, Any],
    tmp_path: Path,
) -> None:
    """TtsSynthesisService.synthesize writes local WAV only — never uploads."""
    script = ConversationScript.model_validate(sample_script_dict)
    client = MagicMock()
    with (
        patch("tts_audio_conversation.logic.tts.pcm_from_wav", return_value=b"\x00\x00"),
        patch("tts_audio_conversation.logic.tts.write_wav") as mock_write,
        patch("tts_audio_conversation.logic.storage.upload_file") as mock_upload,
    ):
        client.synthesize_speech.return_value = MagicMock(audio_content=b"fake")
        dest = tmp_path / "out.wav"
        mock_write.side_effect = lambda path, _frames, _rate: Path(path).write_bytes(b"RIFF")
        result = TtsSynthesisService(client).synthesize(script, dest)
        assert result == dest.resolve()
        mock_upload.assert_not_called()
        assert client.synthesize_speech.called
