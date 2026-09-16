"""Tests for data models and script validation."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from tts_audio_conversation.logic.models import (
    AudioEncoding,
    ConversationMetadata,
    ConversationScript,
    DialogueTurn,
    VoiceConfig,
)


class TestModels:
    """Test suite for Pydantic models in tts_audio_conversation."""

    def test_voice_config_rejects_non_chirp3(self) -> None:
        """Schema rejects voices whose name does not contain Chirp3-HD."""
        with pytest.raises(ValidationError, match="Chirp3-HD"):
            VoiceConfig(name="en-US-Standard-A", language_code="en-US")

    def test_dialogue_turn_valid(self) -> None:
        """Test valid DialogueTurn creation."""
        turn = DialogueTurn(speaker="host", text="Welcome back!", pause_after_ms=400)
        assert turn.speaker == "host"
        assert turn.text == "Welcome back!"
        assert turn.pause_after_ms == 400

    def test_dialogue_turn_empty_text_raises(self) -> None:
        """Test that empty dialogue text raises a validation error."""
        with pytest.raises(ValidationError):
            DialogueTurn(speaker="host", text="   ")

    def test_podcast_script_valid_dict(self, sample_script_dict: dict[str, Any]) -> None:
        """Test creating a valid ConversationScript from dictionary."""
        script = ConversationScript.model_validate(sample_script_dict)
        assert script.metadata.title == "Tech Pulse Europe"
        assert script.metadata.audio_encoding == AudioEncoding.LINEAR16
        assert len(script.turns) == 3
        assert "host" in script.voices
        assert "guest" in script.voices

    def test_podcast_script_from_yaml(self, sample_script_yaml: str) -> None:
        """Test parsing ConversationScript from YAML string."""
        script = ConversationScript.from_yaml(sample_script_yaml)
        assert script.metadata.title == "Tech Pulse Europe"
        assert script.turns[1].speaker == "guest"

    def test_podcast_script_from_json(self, sample_script_json: str) -> None:
        """Test parsing ConversationScript from JSON string."""
        script = ConversationScript.from_json(sample_script_json)
        assert script.metadata.title == "Tech Pulse Europe"
        assert len(script.turns) == 3

    def test_podcast_script_to_yaml(self, sample_script_dict: dict[str, Any]) -> None:
        """Test converting ConversationScript back to YAML string."""
        script = ConversationScript.model_validate(sample_script_dict)
        yaml_out = script.to_yaml()
        assert "Tech Pulse Europe" in yaml_out
        assert "en-US-Chirp3-HD-Fenrir" in yaml_out

    def test_podcast_script_to_json(self, sample_script_dict: dict[str, Any]) -> None:
        """Test converting ConversationScript back to JSON string."""
        script = ConversationScript.model_validate(sample_script_dict)
        json_out = script.to_json()
        assert "Tech Pulse Europe" in json_out

    def test_unregistered_speaker_in_turn_raises(self, sample_script_dict: dict[str, Any]) -> None:
        """Test that a turn referring to an undefined speaker raises validation error."""
        sample_script_dict["turns"].append(
            {
                "speaker": "ghostspeaker",
                "text": "Who am I?",
            }
        )
        with pytest.raises(ValidationError) as excinfo:
            ConversationScript.model_validate(sample_script_dict)
        assert "ghostspeaker" in str(excinfo.value)

    def test_more_than_two_voices_raises(self, sample_script_dict: dict[str, Any]) -> None:
        """Cloud TTS multi-speaker synthesis supports at most two voices."""
        sample_script_dict["voices"]["third"] = {
            "name": "en-US-Chirp3-HD-Charon",
            "language_code": "en-US",
        }
        with pytest.raises(ValidationError):
            ConversationScript.model_validate(sample_script_dict)

    def test_non_alphanumeric_speaker_alias_raises(self) -> None:
        """Cloud TTS aliases cannot contain underscores."""
        with pytest.raises(ValidationError):
            DialogueTurn(speaker="host_1", text="Hello")

    def test_audio_encoding_enum_support(self) -> None:
        """Test supported audio encodings (MP3 and LINEAR16)."""
        meta_mp3 = ConversationMetadata(
            title="Test", language_code="en-US", audio_encoding=AudioEncoding.MP3
        )
        assert meta_mp3.audio_encoding == AudioEncoding.MP3

        meta_wav = ConversationMetadata(
            title="Test", language_code="en-US", audio_encoding=AudioEncoding.LINEAR16
        )
        assert meta_wav.audio_encoding == AudioEncoding.LINEAR16

    def test_character_count_and_turn_metrics(self, sample_script_dict: dict[str, Any]) -> None:
        """Test calculating character count and turn counts."""
        script = ConversationScript.model_validate(sample_script_dict)
        assert script.total_character_count > 50
        assert script.turn_count == 3

    def test_from_payload_yaml_and_json(
        self, sample_script_yaml: str, sample_script_json: str
    ) -> None:
        """YAML and JSON payloads both parse; JSON is detected by a leading brace."""
        yaml_script = ConversationScript.from_payload(sample_script_yaml)
        json_script = ConversationScript.from_payload(sample_script_json)
        assert yaml_script.metadata.title == json_script.metadata.title

    def test_from_payload_rejects_oversize(self, sample_script_yaml: str) -> None:
        """Byte cap is enforced before parsing."""
        from tts_audio_conversation.logic.exceptions import ScriptPayloadError

        with pytest.raises(ScriptPayloadError, match="bytes"):
            ConversationScript.from_payload(sample_script_yaml, max_bytes=8)

    def test_from_payload_rejects_empty(self) -> None:
        """Empty payloads are rejected."""
        from tts_audio_conversation.logic.exceptions import ScriptPayloadError

        with pytest.raises(ScriptPayloadError, match="empty"):
            ConversationScript.from_payload("   ")
