"""Result types for the public ``AudioConversationService`` facade."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class ScriptValidationResult(BaseModel):
    """Soft outcome of validating a script at a ``gs://`` URI."""

    valid: bool
    title: str = ""
    language_code: str = ""
    speakers: list[str] = Field(default_factory=list)
    turn_count: int = 0
    total_characters: int = 0
    script_uri: str = Field(description="The validated gs:// URI.")
    error: str | None = None


class ScriptUploadResult(BaseModel):
    """Outcome of uploading a script into the staging script repository."""

    script_uri: str = Field(description="gs:// URI of script.yaml.")
    script_id: str = Field(description="UUID folder segment under …/scripts/.")


class TranslateScriptResult(BaseModel):
    """Outcome of translating a script stored in GCS."""

    output_uri: str = Field(
        description="gs:// URI of the translated script (or input when skipped)."
    )
    language_code: str
    title: str = ""
    skipped: bool = False


class VoiceInfo(BaseModel):
    """One Chirp 3 HD voice from the EU catalog."""

    name: str
    language_code: str
    gender: str


class DownloadResult(BaseModel):
    """Outcome of downloading a GCS object to a local path."""

    local_path: Path
    gcs_uri: str
