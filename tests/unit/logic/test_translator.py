"""Tests for Cloud Translation client interacting with EU endpoint."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from tts_audio_conversation.logic.models import ConversationScript
from tts_audio_conversation.logic.translator import (
    EU_TRANSLATE_ENDPOINT,
    ConversationTranslator,
    batch_translate_texts,
)


class TestConversationTranslator:
    """Test suite for ConversationTranslator EU regional translation."""

    def test_eu_translate_endpoint_constant(self) -> None:
        """Verify the regional translate endpoint is translate-eu."""
        assert EU_TRANSLATE_ENDPOINT == "translate-eu.googleapis.com"

    @patch("tts_audio_conversation.logic.translator.translate_v3.TranslationServiceClient")
    def test_client_initialization_with_eu_endpoint(
        self, mock_trans_cls: MagicMock, mock_credentials: MagicMock
    ) -> None:
        """Verify translator client options enforce EU regional endpoint."""
        translator = ConversationTranslator(project_id="test-proj", credentials=mock_credentials)
        assert translator.project_id == "test-proj"
        mock_trans_cls.assert_called_once()
        _, kwargs = mock_trans_cls.call_args
        assert kwargs["client_options"].api_endpoint == "translate-eu.googleapis.com"

    @patch("tts_audio_conversation.logic.translator.translate_v3.TranslationServiceClient")
    def test_translate_script_batch_turns(
        self,
        mock_trans_cls: MagicMock,
        mock_credentials: MagicMock,
        sample_script_dict: dict[str, Any],
    ) -> None:
        """Verify translation of dialogue turns preserves structure and maps voices."""
        mock_client = MagicMock()
        mock_resp = MagicMock()

        t1 = MagicMock()
        t1.translated_text = "Willkommen bei Tech Pulse Europe. Heute erkunden wir Cloud TTS."
        t2 = MagicMock()
        t2.translated_text = "Hallo! Ich freue mich, dabei zu sein."
        t3 = MagicMock()
        t3.translated_text = "Lassen Sie uns in die Datensouveränität in der EU eintauchen."
        mock_resp.translations = [t1, t2, t3]
        mock_client.translate_text.return_value = mock_resp
        mock_trans_cls.return_value = mock_client

        translator = ConversationTranslator(project_id="test-proj", credentials=mock_credentials)
        script = ConversationScript.model_validate(sample_script_dict)

        translated = translator.translate_script(script=script, target_language_code="de-DE")

        assert translated.metadata.language_code == "de-DE"
        assert len(translated.turns) == 3
        assert (
            translated.turns[0].text
            == "Willkommen bei Tech Pulse Europe. Heute erkunden wir Cloud TTS."
        )
        assert translated.turns[0].speaker == "host"
        assert translated.turns[0].pause_after_ms == 300
        assert translated.turns[1].speaker == "guest"
        assert translated.turns[1].pause_after_ms == 500

        request = mock_client.translate_text.call_args.kwargs["request"]
        assert request["source_language_code"] == "en"
        assert request["target_language_code"] == "de"
        assert request["mime_type"] == "text/plain"

        # Voices remapped to German Chirp 3 HD
        assert translated.voices["host"].name == "de-DE-Chirp3-HD-Fenrir"
        assert translated.voices["host"].language_code == "de-DE"
        assert translated.voices["guest"].name == "de-DE-Chirp3-HD-Aoede"
        assert translated.voices["guest"].language_code == "de-DE"

    def test_voice_mapping_fallback_logic(self, mock_credentials: MagicMock) -> None:
        """Verify fallback when original voice name pattern is non-standard."""
        with patch("tts_audio_conversation.logic.translator.translate_v3.TranslationServiceClient"):
            translator = ConversationTranslator(
                project_id="test-proj", credentials=mock_credentials
            )
            mapped_voice = translator.map_voice_name(
                current_voice_name="en-US-Chirp3-HD-Fenrir",
                target_lang="fr-FR",
            )
            assert mapped_voice == "fr-FR-Chirp3-HD-Fenrir"

    def test_batch_translate_texts_splits_on_budget(self) -> None:
        """Oversized turn lists are packed into multiple RPCs."""
        texts = ["aaaa", "bbbb", "cccc"]
        batches = batch_translate_texts(texts, max_chars=6)
        assert batches == [["aaaa"], ["bbbb"], ["cccc"]]
        combined = batch_translate_texts(texts, max_chars=8)
        assert combined == [["aaaa", "bbbb"], ["cccc"]]

    @patch("tts_audio_conversation.logic.translator.translate_v3.TranslationServiceClient")
    def test_translate_script_uses_multiple_rpcs(
        self,
        mock_trans_cls: MagicMock,
        mock_credentials: MagicMock,
        sample_script_dict: dict[str, Any],
    ) -> None:
        """Character budget forces more than one translate_text call."""
        sample_script_dict["turns"] = [
            {"speaker": "host", "text": "AAAAAAAAAA", "pause_after_ms": 100},
            {"speaker": "guest", "text": "BBBBBBBBBB", "pause_after_ms": 200},
            {"speaker": "host", "text": "CCCCCCCCCC"},
        ]
        mock_client = MagicMock()

        def fake_translate(*, request: dict[str, Any], retry: object) -> MagicMock:
            resp = MagicMock()
            resp.translations = [
                MagicMock(translated_text=f"T-{item}") for item in request["contents"]
            ]
            return resp

        mock_client.translate_text.side_effect = fake_translate
        mock_trans_cls.return_value = mock_client
        translator = ConversationTranslator(project_id="test-proj", credentials=mock_credentials)
        script = ConversationScript.model_validate(sample_script_dict)
        translated = translator.translate_script(script, "de-DE", max_chars=12)
        assert mock_client.translate_text.call_count >= 2
        assert translated.turn_count == 3
        assert translated.turns[0].pause_after_ms == 100
        assert translated.turns[1].pause_after_ms == 200
        assert translated.turns[0].text == "T-AAAAAAAAAA"
