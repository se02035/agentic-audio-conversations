"""Compatibility re-exports for TTS helpers (prefer ``tts`` / ``voices`` / ``batching``)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from google.cloud import texttospeech
from opentelemetry import trace

from tts_audio_conversation.logic.batching import (
    DEFAULT_BATCH_PAUSE_MS,
    LINEAR16_SAMPLE_WIDTH,
    MAX_BATCH_CHARS,
    batch_turns,
    pcm_from_wav,
    silence_pcm,
    split_long_text,
    write_wav,
)
from tts_audio_conversation.logic.models import ConversationScript
from tts_audio_conversation.logic.storage import upload_file
from tts_audio_conversation.logic.tts import (
    EU_TTS_ENDPOINT,
    GEMINI_TTS_MODEL,
    TTS_RETRY,
    TTS_RETRY_PREDICATE,
    TtsSynthesisService,
    eu_tts_client,
    extract_speaker_persona,
    speaker_voice_configs,
)
from tts_audio_conversation.logic.voices import (
    assert_voices_in_catalog,
    list_chirp3_voices,
)

tracer = trace.get_tracer(__name__)

__all__ = [
    "DEFAULT_BATCH_PAUSE_MS",
    "EU_TTS_ENDPOINT",
    "GEMINI_TTS_MODEL",
    "LINEAR16_SAMPLE_WIDTH",
    "MAX_BATCH_CHARS",
    "TTS_RETRY",
    "TTS_RETRY_PREDICATE",
    "TtsSynthesisService",
    "assert_voices_in_catalog",
    "batch_turns",
    "eu_tts_client",
    "extract_speaker_persona",
    "list_chirp3_voices",
    "pcm_from_wav",
    "silence_pcm",
    "speaker_voice_configs",
    "split_long_text",
    "synthesize_script",
    "write_wav",
]


def synthesize_script(
    client: texttospeech.TextToSpeechClient,
    script: ConversationScript,
    output_path: Path | str,
    *,
    output_gcs_uri: str | None = None,
    gcs_client: Any | None = None,
    max_batch_chars: int = MAX_BATCH_CHARS,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Synthesize locally via ``TtsSynthesisService``, optionally upload to GCS.

    Prefer ``TtsSynthesisService.synthesize`` for new code (no GCS). This wrapper
    preserves the historical CLI/MCP upload behavior until the pipeline lands.
    """
    dest = TtsSynthesisService(client, max_batch_chars=max_batch_chars).synthesize(
        script,
        output_path,
        should_cancel=should_cancel,
        on_progress=on_progress,
    )
    if output_gcs_uri:
        if should_cancel is not None and should_cancel():
            from tts_audio_conversation.logic.exceptions import SynthesisCancelled

            raise SynthesisCancelled()
        if gcs_client is None:
            raise ValueError("gcs_client is required when output_gcs_uri is set.")
        with tracer.start_as_current_span("gcs.upload"):
            upload_file(gcs_client, output_gcs_uri, dest)
    return dest
