"""One background conversation synthesis unit (Command)."""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opentelemetry import trace

from tts_audio_conversation.logic.exceptions import SynthesisCancelled
from tts_audio_conversation.logic.models import ConversationScript

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


@dataclass
class CloudHandles:
    """Memoized SDK clients used by the job manager and workers."""

    tts_client: Any
    gcs_client: Any


ClientsFactory = Callable[[], CloudHandles]
SynthesizeFn = Callable[..., Path]
UploadFileFn = Callable[[Any, str, Path], None]
DeleteFileFn = Callable[[Any, str], None]
VoiceCatalogFn = Callable[[ConversationScript, CloudHandles], None]


class ConversationCreationJob:
    """Run synthesize → upload. Does not persist job status or translate."""

    def __init__(
        self,
        job_id: str,
        script: ConversationScript,
        *,
        audio_uri: str,
        should_cancel: Callable[[], bool],
        tts_client: Any,
        gcs_client: Any,
        synthesize_fn: SynthesizeFn,
        upload_file_fn: UploadFileFn,
        delete_file_fn: DeleteFileFn,
    ) -> None:
        """Bind work inputs and I/O callables."""
        self._job_id = job_id
        self._script = script
        self._audio_uri = audio_uri
        self._should_cancel = should_cancel
        self._tts_client = tts_client
        self._gcs_client = gcs_client
        self._synthesize = synthesize_fn
        self._upload_file = upload_file_fn
        self._delete_file = delete_file_fn

    def execute(self) -> None:
        """Synthesize and upload, or raise ``SynthesisCancelled``."""
        if self._should_cancel():
            raise SynthesisCancelled()

        with tempfile.TemporaryDirectory(prefix=f"conversation-{self._job_id}-") as tmp:
            wav_path = Path(tmp) / "audio.wav"
            self._synthesize(
                self._tts_client,
                self._script,
                wav_path,
                should_cancel=self._should_cancel,
            )
            if self._should_cancel():
                raise SynthesisCancelled()
            with tracer.start_as_current_span("gcs.upload"):
                self._upload_file(self._gcs_client, self._audio_uri, wav_path)
            if self._should_cancel():
                try:
                    self._delete_file(self._gcs_client, self._audio_uri)
                except Exception:
                    logger.exception("Failed to delete cancelled audio %s", self._audio_uri)
                raise SynthesisCancelled()
