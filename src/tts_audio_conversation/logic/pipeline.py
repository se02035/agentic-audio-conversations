"""Orchestrate voice preflight, local TTS, and optional GCS upload."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tts_audio_conversation.logic.exceptions import SynthesisCancelled
from tts_audio_conversation.logic.models import ConversationScript
from tts_audio_conversation.logic.storage import StorageService
from tts_audio_conversation.logic.tts import TtsSynthesisService
from tts_audio_conversation.logic.voices import VoiceCatalogService


@dataclass(frozen=True)
class PipelineResult:
    """Outcome of a pipeline run."""

    local_path: Path
    gcs_uri: str | None = None


class ConversationPipeline:
    """Preflight voices → synthesize WAV → optional upload (no translation)."""

    def __init__(
        self,
        tts: TtsSynthesisService,
        storage: StorageService,
        voices: VoiceCatalogService,
    ) -> None:
        """Wire TTS, storage, and voice-catalog collaborators."""
        self._tts = tts
        self._storage = storage
        self._voices = voices

    def preflight_voices(self, script: ConversationScript) -> None:
        """Assert Chirp 3 HD voices via EU ``list_voices`` (no synthesis)."""
        self._voices.assert_in_catalog(script)

    def run(
        self,
        script: ConversationScript,
        *,
        output_path: Path,
        output_gcs_uri: str | None = None,
        should_cancel: Callable[[], bool] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> PipelineResult:
        """Assert voices, synthesize locally, then optionally upload.

        Args:
            script: Already-prepared script (caller translates first if needed).
            output_path: Local WAV destination.
            output_gcs_uri: Optional ``gs://`` upload target.
            should_cancel: Cooperative cancel between batches / before upload.
            on_progress: Optional batch progress callback.

        Returns:
            Local path and optional GCS URI.

        Raises:
            SynthesisCancelled: When cancel is observed; deletes uploaded blob
                if cancel happens after upload.
        """
        self.preflight_voices(script)
        local_path = self._tts.synthesize(
            script,
            output_path,
            should_cancel=should_cancel,
            on_progress=on_progress,
        )
        if output_gcs_uri is None:
            return PipelineResult(local_path=local_path)

        if should_cancel is not None and should_cancel():
            raise SynthesisCancelled()

        try:
            self._storage.upload_file(output_gcs_uri, local_path)
        except Exception:
            raise

        if should_cancel is not None and should_cancel():
            try:
                self._storage.delete_file(output_gcs_uri)
            except Exception:
                pass
            raise SynthesisCancelled()

        return PipelineResult(local_path=local_path, gcs_uri=output_gcs_uri)
