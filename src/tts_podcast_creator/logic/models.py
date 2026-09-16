"""Podcast script schema: speakers, turns, and episode metadata."""

from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from tts_podcast_creator.logic.exceptions import ScriptPayloadError

_ALIAS_RE = re.compile(r"^[a-zA-Z0-9]+$")


class AudioEncoding(StrEnum):
    """Supported audio encodings. Synthesis always writes LINEAR16 WAV."""

    MP3 = "MP3"
    LINEAR16 = "LINEAR16"


class VoiceConfig(BaseModel):
    """Chirp 3 HD voice for one speaker alias."""

    name: str = Field(..., description="Full voice identifier, e.g. 'en-US-Chirp3-HD-Fenrir'")
    language_code: str = Field(..., description="BCP-47 language code, e.g. 'en-US'")

    @field_validator("name")
    @classmethod
    def require_chirp3_hd(cls, v: str) -> str:
        """Reject voices that are not Chirp 3 HD (schema-only, no ADC)."""
        if "Chirp3-HD" not in v:
            raise ValueError(
                f"Voice '{v}' must be a Chirp 3 HD voice (name must contain 'Chirp3-HD')."
            )
        return v


class DialogueTurn(BaseModel):
    """One spoken turn in the script."""

    speaker: str = Field(..., description="Speaker alias mapped to a voice definition")
    text: str = Field(..., min_length=1, description="Spoken dialogue text")
    pause_after_ms: int | None = Field(
        default=None,
        ge=0,
        description="Silence after this turn, in milliseconds, applied when stitching batches",
    )

    @field_validator("speaker")
    @classmethod
    def validate_speaker_alias(cls, v: str) -> str:
        """Require alphanumeric speaker aliases (Cloud TTS constraint)."""
        if not _ALIAS_RE.fullmatch(v):
            raise ValueError(
                f"Speaker alias '{v}' must be alphanumeric [a-zA-Z0-9]+ "
                "(no spaces, dashes, or underscores)."
            )
        return v

    @field_validator("text")
    @classmethod
    def validate_non_empty_text(cls, v: str) -> str:
        """Reject blank or whitespace-only text."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("Dialogue turn text cannot be empty or only whitespace.")
        return stripped


class PodcastMetadata(BaseModel):
    """Episode-level metadata and audio settings."""

    title: str = Field(..., description="Episode title")
    description: str | None = Field(default=None, description="Optional episode description")
    language_code: str = Field(default="en-US", description="Primary BCP-47 language code")
    audio_encoding: AudioEncoding = Field(
        default=AudioEncoding.LINEAR16,
        description="Declared encoding; synthesis always writes LINEAR16 WAV",
    )
    sample_rate_hertz: int = Field(
        default=24000,
        ge=8000,
        le=48000,
        description="Audio sampling rate in Hertz",
    )


class PodcastScript(BaseModel):
    """Validated podcast script: metadata, voices, and ordered turns."""

    metadata: PodcastMetadata = Field(..., description="Episode metadata")
    voices: dict[str, VoiceConfig] = Field(..., min_length=1, description="Speaker voice mappings")
    turns: list[DialogueTurn] = Field(..., min_length=1, description="Ordered dialogue turns")

    @model_validator(mode="after")
    def validate_speakers(self) -> PodcastScript:
        """Ensure voice aliases are alphanumeric and every turn speaker is declared."""
        for alias in self.voices:
            if not _ALIAS_RE.fullmatch(alias):
                raise ValueError(
                    f"Speaker alias '{alias}' must be alphanumeric [a-zA-Z0-9]+ "
                    "(no spaces, dashes, or underscores)."
                )
        declared = set(self.voices)
        for idx, turn in enumerate(self.turns):
            if turn.speaker not in declared:
                raise ValueError(
                    f"Turn {idx + 1} references undeclared speaker '{turn.speaker}'. "
                    f"Declared speakers: {sorted(declared)}"
                )
        return self

    @property
    def total_character_count(self) -> int:
        """Total characters across all dialogue turns."""
        return sum(len(turn.text) for turn in self.turns)

    @property
    def turn_count(self) -> int:
        """Number of dialogue turns."""
        return len(self.turns)

    @classmethod
    def from_path(cls, path: Path | str) -> PodcastScript:
        """Load a script from a YAML or JSON file."""
        script_path = Path(path)
        content = script_path.read_text(encoding="utf-8")
        if script_path.suffix.lower() in {".yaml", ".yml"}:
            return cls.from_yaml(content)
        return cls.from_json(content)

    @classmethod
    def from_yaml(cls, yaml_content: str) -> PodcastScript:
        """Parse a PodcastScript from a YAML string."""
        data = yaml.safe_load(yaml_content)
        if not isinstance(data, dict):
            raise ValueError("YAML content must deserialize into a dictionary.")
        return cls.model_validate(data)

    def to_yaml(self) -> str:
        """Serialize this script to YAML."""
        return yaml.dump(self.model_dump(mode="json"), sort_keys=False, allow_unicode=True)

    @classmethod
    def from_json(cls, json_content: str) -> PodcastScript:
        """Parse a PodcastScript from a JSON string."""
        return cls.model_validate(json.loads(json_content))

    def to_json(self) -> str:
        """Serialize this script to indented JSON."""
        return self.model_dump_json(indent=2)

    @classmethod
    def from_payload(cls, payload: str, *, max_bytes: int | None = None) -> PodcastScript:
        """Parse a YAML or JSON script string, optionally enforcing a byte cap.

        JSON is detected when the stripped payload starts with ``{``.

        Args:
            payload: YAML or JSON document.
            max_bytes: Maximum UTF-8 size. ``None`` disables the cap.

        Returns:
            Validated script.

        Raises:
            ScriptPayloadError: Empty, oversized, or unparsable payload.
        """
        if payload is None:
            raise ScriptPayloadError("Script payload is empty.")
        encoded = payload.encode("utf-8")
        if max_bytes is not None and len(encoded) > max_bytes:
            raise ScriptPayloadError(
                f"Script payload is {len(encoded)} bytes; "
                f"max is {max_bytes} (PODCAST_MAX_SCRIPT_BYTES)."
            )
        stripped = payload.strip()
        if not stripped:
            raise ScriptPayloadError("Script payload is empty.")
        try:
            if stripped.startswith("{"):
                return cls.from_json(stripped)
            return cls.from_yaml(stripped)
        except ScriptPayloadError:
            raise
        except Exception as exc:
            raise ScriptPayloadError(f"Invalid script payload: {exc}") from exc
