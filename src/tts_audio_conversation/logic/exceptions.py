"""Domain exceptions for synthesis and MCP jobs."""

from __future__ import annotations


class SynthesisCancelled(Exception):
    """Raised when a conversation job is cancelled between Cloud TTS batches."""


class JobNotFound(LookupError):
    """Raised when a conversation job_id is unknown to this process and GCS."""

    def __init__(self, job_id: str) -> None:
        """Record the missing job identifier."""
        self.job_id = job_id
        super().__init__(f"Unknown job_id '{job_id}'.")


class ScriptPayloadError(ValueError):
    """Raised when a YAML/JSON script payload is missing, oversized, or invalid."""


class VoiceCatalogError(ValueError):
    """Raised when a Chirp 3 HD voice is missing from the EU catalog."""
