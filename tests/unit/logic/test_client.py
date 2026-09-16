"""Tests for Cloud TTS helpers using the official EU TextToSpeechClient."""

from __future__ import annotations

import io
import wave
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from google.cloud import texttospeech

from tts_audio_conversation.logic.client import (
    EU_TTS_ENDPOINT,
    MAX_BATCH_CHARS,
    TTS_RETRY,
    TTS_RETRY_PREDICATE,
    assert_voices_in_catalog,
    batch_turns,
    eu_tts_client,
    list_chirp3_voices,
    speaker_voice_configs,
    split_long_text,
    synthesize_script,
)
from tts_audio_conversation.logic.exceptions import VoiceCatalogError
from tts_audio_conversation.logic.models import ConversationScript, DialogueTurn


def _wav_bytes(n_frames: int = 240, sample_rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_out:
        wav_out.setnchannels(1)
        wav_out.setsampwidth(2)
        wav_out.setframerate(sample_rate)
        wav_out.writeframes(b"\x00\x00" * n_frames)
    return buf.getvalue()


class TestPodcastTTSClient:
    """Tests for EU TTS synthesis helpers."""

    def test_regional_endpoint_constant(self) -> None:
        """EU TTS endpoint stays pinned."""
        assert EU_TTS_ENDPOINT == "eu-texttospeech.googleapis.com"
        assert MAX_BATCH_CHARS == 1500

    def test_eu_tts_client_uses_eu_endpoint(self, mock_credentials: MagicMock) -> None:
        """ClientOptions must target eu-texttospeech."""
        with patch(
            "tts_audio_conversation.logic.client.texttospeech.TextToSpeechClient"
        ) as mock_cls:
            eu_tts_client(mock_credentials)
        _, kwargs = mock_cls.call_args
        assert kwargs["client_options"].api_endpoint == EU_TTS_ENDPOINT

    def test_retry_predicate_covers_transient_errors(self) -> None:
        """GAPIC retry predicate includes 429/502/503/504."""
        from google.api_core import exceptions

        assert TTS_RETRY_PREDICATE(exceptions.ResourceExhausted("429"))
        assert TTS_RETRY_PREDICATE(exceptions.BadGateway("502"))
        assert TTS_RETRY_PREDICATE(exceptions.ServiceUnavailable("503"))
        assert TTS_RETRY_PREDICATE(exceptions.DeadlineExceeded("504"))
        assert not TTS_RETRY_PREDICATE(exceptions.InvalidArgument("bad"))

    def test_companion_voice_for_single_speaker(self, sample_script_dict: dict[str, Any]) -> None:
        """Single-speaker scripts get an unused Companion persona."""
        sample_script_dict["voices"] = {
            "narrator": {
                "name": "en-US-Chirp3-HD-Fenrir",
                "language_code": "en-US",
            }
        }
        sample_script_dict["turns"] = [
            {"speaker": "narrator", "text": "Once upon a time."},
        ]
        script = ConversationScript.model_validate(sample_script_dict)
        configs, alias_map = speaker_voice_configs(script)
        assert alias_map["narrator"] == "narrator"
        assert len(configs) == 2
        assert {cfg.speaker_alias for cfg in configs} == {"narrator", "Companion"}
        assert {cfg.speaker_id for cfg in configs} == {"Fenrir", "Aoede"}

    def test_batch_turns_respects_char_cap(self) -> None:
        """Batches stay at or under MAX_BATCH_CHARS unless a flush is forced."""
        turns = [
            DialogueTurn(speaker="host", text="a" * 800),
            DialogueTurn(speaker="guest", text="b" * 800),
        ]
        batches = batch_turns(turns, max_batch_chars=1500)
        assert len(batches) == 2
        assert batches[0][0].text == "a" * 800

    def test_batch_turns_flushes_on_pause(self) -> None:
        """A turn with pause_after_ms starts a new batch so silence can be inserted."""
        turns = [
            DialogueTurn(speaker="host", text="Hello.", pause_after_ms=400),
            DialogueTurn(speaker="guest", text="Hi there."),
        ]
        batches = batch_turns(turns, max_batch_chars=1500)
        assert len(batches) == 2
        assert batches[0][0].pause_after_ms == 400

    def test_split_long_text_on_spaces(self) -> None:
        """Oversized turns split on word boundaries."""
        text = ("word " * 400).strip()
        parts = split_long_text(text, max_chars=20)
        assert all(len(part) <= 20 for part in parts)
        assert "".join(parts).replace(" ", "") == text.replace(" ", "")

    def test_split_long_text_rejects_non_positive_max_chars(self) -> None:
        """max_chars must be at least 1 before any splitting."""
        with pytest.raises(ValueError, match="max_chars"):
            split_long_text("hello", max_chars=0)

    def test_batch_turns_respects_utf8_byte_cap(self) -> None:
        """Multi-byte text is split so MultiSpeakerMarkup stays under 4000 UTF-8 bytes."""
        from tts_audio_conversation.logic.batching import markup_utf8_bytes

        # Each "ü" is 2 UTF-8 bytes; 1200 chars ≈ 2400 bytes of text alone.
        turn = DialogueTurn(speaker="host", text="ü" * 2200)
        batches = batch_turns([turn], max_batch_chars=1500)
        assert len(batches) >= 2
        for batch in batches:
            assert sum(len(t.text) for t in batch) <= 1500
            assert markup_utf8_bytes(batch) <= 4000

    def test_synthesize_script_stitches_wav(
        self,
        tmp_path: Path,
        sample_script_dict: dict[str, Any],
    ) -> None:
        """synthesize_speech is called per batch and a WAV is written."""
        script = ConversationScript.model_validate(sample_script_dict)
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.audio_content = _wav_bytes()
        mock_client.synthesize_speech.return_value = mock_response

        dest = tmp_path / "episode.wav"
        result = synthesize_script(mock_client, script, dest)

        assert result == dest.resolve()
        assert dest.exists()
        assert dest.stat().st_size > 0
        assert mock_client.synthesize_speech.call_count >= 1
        call_kwargs = mock_client.synthesize_speech.call_args.kwargs
        assert call_kwargs["retry"] is TTS_RETRY
        assert call_kwargs["voice"].model_name == "gemini-2.5-flash-tts"
        with wave.open(str(dest), "rb") as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getframerate() == 24000

    def test_synthesize_script_uploads_when_gcs_uri_set(
        self,
        tmp_path: Path,
        sample_script_dict: dict[str, Any],
    ) -> None:
        """Optional GCS upload uses the official storage helper."""
        script = ConversationScript.model_validate(sample_script_dict)
        mock_tts = MagicMock()
        mock_tts.synthesize_speech.return_value = MagicMock(audio_content=_wav_bytes())
        mock_gcs = MagicMock()

        with patch("tts_audio_conversation.logic.client.upload_file") as mock_upload:
            synthesize_script(
                mock_tts,
                script,
                tmp_path / "episode.wav",
                output_gcs_uri="gs://eu-bucket/conversation/ep.wav",
                gcs_client=mock_gcs,
            )
        mock_upload.assert_called_once()
        assert mock_upload.call_args.args[1] == "gs://eu-bucket/conversation/ep.wav"

    def test_synthesize_script_requires_gcs_client_for_upload(
        self,
        tmp_path: Path,
        sample_script_dict: dict[str, Any],
    ) -> None:
        """Uploads no longer construct a default storage.Client()."""
        script = ConversationScript.model_validate(sample_script_dict)
        mock_tts = MagicMock()
        mock_tts.synthesize_speech.return_value = MagicMock(audio_content=_wav_bytes())
        with pytest.raises(ValueError, match="gcs_client is required"):
            synthesize_script(
                mock_tts,
                script,
                tmp_path / "episode.wav",
                output_gcs_uri="gs://eu-bucket/conversation/ep.wav",
            )

    def test_synthesize_script_honours_should_cancel(
        self,
        tmp_path: Path,
        sample_script_dict: dict[str, Any],
    ) -> None:
        """Cancel between batches skips remaining synthesize_speech calls and WAV write."""
        from tts_audio_conversation.logic.exceptions import SynthesisCancelled

        sample_script_dict["turns"] = [
            {"speaker": "host", "text": "Hello.", "pause_after_ms": 100},
            {"speaker": "guest", "text": "Hi there.", "pause_after_ms": 100},
            {"speaker": "host", "text": "Goodbye."},
        ]
        script = ConversationScript.model_validate(sample_script_dict)
        mock_client = MagicMock()
        mock_client.synthesize_speech.return_value = MagicMock(audio_content=_wav_bytes())
        dest = tmp_path / "cancelled.wav"
        calls = {"n": 0}

        def should_cancel() -> bool:
            return calls["n"] >= 1

        def on_progress(_idx: int, _total: int) -> None:
            calls["n"] += 1

        with pytest.raises(SynthesisCancelled):
            synthesize_script(
                mock_client,
                script,
                dest,
                should_cancel=should_cancel,
                on_progress=on_progress,
            )
        assert not dest.exists()
        assert mock_client.synthesize_speech.call_count == 1

    def test_list_chirp3_voices_filters(self) -> None:
        """Only Chirp3-HD voices are returned."""
        chirp = MagicMock()
        chirp.name = "de-DE-Chirp3-HD-Fenrir"
        chirp.language_codes = ["de-DE"]
        chirp.ssml_gender = texttospeech.SsmlVoiceGender.MALE
        other = MagicMock()
        other.name = "de-DE-Standard-A"
        other.language_codes = ["de-DE"]
        other.ssml_gender = texttospeech.SsmlVoiceGender.FEMALE

        mock_client = MagicMock()
        mock_client.list_voices.return_value = MagicMock(voices=[chirp, other])
        results = list_chirp3_voices(mock_client, language_code="de-DE")
        assert len(results) == 1
        assert results[0]["name"] == "de-DE-Chirp3-HD-Fenrir"
        mock_client.list_voices.assert_called_once_with(language_code="de-DE")

    def test_assert_voices_in_catalog_accepts_known_names(
        self, sample_script_dict: dict[str, Any]
    ) -> None:
        """Catalog preflight passes when every script voice is listed."""
        script = ConversationScript.model_validate(sample_script_dict)

        def fake_list(_client: object, language_code: str | None = None) -> list[dict[str, str]]:
            assert language_code == "en-US"
            return [
                {"name": "en-US-Chirp3-HD-Fenrir", "language_code": "en-US", "gender": "MALE"},
                {"name": "en-US-Chirp3-HD-Aoede", "language_code": "en-US", "gender": "FEMALE"},
            ]

        assert_voices_in_catalog(MagicMock(), script, list_voices_fn=fake_list)

    def test_assert_voices_in_catalog_rejects_unknown(
        self, sample_script_dict: dict[str, Any]
    ) -> None:
        """Unknown Chirp 3 names fail before synthesize_speech."""
        script = ConversationScript.model_validate(sample_script_dict)

        def fake_list(_client: object, language_code: str | None = None) -> list[dict[str, str]]:
            return [
                {"name": "en-US-Chirp3-HD-Aoede", "language_code": "en-US", "gender": "FEMALE"},
            ]

        with pytest.raises(VoiceCatalogError, match="Fenrir"):
            assert_voices_in_catalog(MagicMock(), script, list_voices_fn=fake_list)

    def test_assert_voices_in_catalog_checks_companion(
        self, sample_script_dict: dict[str, Any]
    ) -> None:
        """Single-speaker scripts require the injected Companion persona in the catalog."""
        sample_script_dict["voices"] = {
            "narrator": {"name": "en-US-Chirp3-HD-Fenrir", "language_code": "en-US"}
        }
        sample_script_dict["turns"] = [{"speaker": "narrator", "text": "Once upon a time."}]
        script = ConversationScript.model_validate(sample_script_dict)

        def fake_list(_client: object, language_code: str | None = None) -> list[dict[str, str]]:
            return [
                {"name": "en-US-Chirp3-HD-Fenrir", "language_code": "en-US", "gender": "MALE"},
            ]

        with pytest.raises(VoiceCatalogError, match="Aoede"):
            assert_voices_in_catalog(MagicMock(), script, list_voices_fn=fake_list)

    def test_assert_voices_in_catalog_fails_on_list_error(
        self, sample_script_dict: dict[str, Any]
    ) -> None:
        """ADC/list_voices failures fail fast."""
        script = ConversationScript.model_validate(sample_script_dict)

        def boom(_client: object, language_code: str | None = None) -> list[dict[str, str]]:
            raise RuntimeError("429")

        with pytest.raises(VoiceCatalogError, match="Failed to list"):
            assert_voices_in_catalog(MagicMock(), script, list_voices_fn=boom)
