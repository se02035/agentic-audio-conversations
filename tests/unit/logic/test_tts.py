"""Unit tests for TtsSynthesisService (no GCS)."""

from __future__ import annotations

import io
import wave
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from tts_audio_conversation.logic.models import ConversationScript
from tts_audio_conversation.logic.tts import TtsSynthesisService


def _wav_bytes(n_frames: int = 240, sample_rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_out:
        wav_out.setnchannels(1)
        wav_out.setsampwidth(2)
        wav_out.setframerate(sample_rate)
        wav_out.writeframes(b"\x00\x00" * n_frames)
    return buf.getvalue()


def test_tts_synthesis_service_does_not_call_gcs(
    sample_script_dict: dict[str, Any],
    tmp_path: Path,
) -> None:
    """TtsSynthesisService.synthesize writes local WAV only — never uploads."""
    script = ConversationScript.model_validate(sample_script_dict)
    client = MagicMock()
    with patch("tts_audio_conversation.logic.storage.upload_file") as mock_upload:
        client.synthesize_speech.return_value = MagicMock(audio_content=_wav_bytes())
        dest = tmp_path / "out.wav"
        result = TtsSynthesisService(client).synthesize(script, dest)
        assert result == dest.resolve()
        mock_upload.assert_not_called()
        assert client.synthesize_speech.called
        with wave.open(str(dest), "rb") as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getnframes() > 0
            assert wav_file.getframerate() == script.metadata.sample_rate_hertz
