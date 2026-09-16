"""Batched multi-speaker synthesis via the official EU Cloud TTS client."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path

from google.api_core import exceptions, retry
from google.api_core.client_options import ClientOptions
from google.auth.credentials import Credentials
from google.cloud import texttospeech
from opentelemetry import trace

from tts_audio_conversation.logic.batching import (
    DEFAULT_BATCH_PAUSE_MS,
    MAX_BATCH_CHARS,
    batch_turns,
    pcm_from_wav,
    silence_pcm,
    write_wav,
)
from tts_audio_conversation.logic.exceptions import SynthesisCancelled
from tts_audio_conversation.logic.models import ConversationScript

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

EU_TTS_ENDPOINT = "eu-texttospeech.googleapis.com"
GEMINI_TTS_MODEL = "gemini-2.5-flash-tts"

TTS_RETRY_PREDICATE = retry.if_exception_type(
    exceptions.ResourceExhausted,
    exceptions.TooManyRequests,
    exceptions.ServiceUnavailable,
    exceptions.InternalServerError,
    exceptions.BadGateway,
    exceptions.DeadlineExceeded,
    exceptions.GatewayTimeout,
)
TTS_RETRY = retry.Retry(
    predicate=TTS_RETRY_PREDICATE,
    initial=1.0,
    maximum=60.0,
    multiplier=2.0,
    timeout=180.0,
)


def eu_tts_client(credentials: Credentials) -> texttospeech.TextToSpeechClient:
    """Build a TextToSpeechClient pinned to the EU regional endpoint.

    Args:
        credentials: Application Default Credentials.

    Returns:
        Official ``TextToSpeechClient`` targeting ``eu-texttospeech.googleapis.com``.
    """
    return texttospeech.TextToSpeechClient(
        credentials=credentials,
        client_options=ClientOptions(api_endpoint=EU_TTS_ENDPOINT),
    )


def extract_speaker_persona(voice_name: str) -> str:
    """Return the Chirp 3 persona (Aoede, Fenrir, ...) from a voice name."""
    if "-" in voice_name:
        return voice_name.split("-")[-1]
    return voice_name


def speaker_voice_configs(
    script: ConversationScript,
) -> tuple[list[texttospeech.MultispeakerPrebuiltVoice], dict[str, str]]:
    """Build MultiSpeakerVoiceConfig entries and an alias map.

    Cloud TTS requires at least two speaker definitions, so a one-speaker script
    gets an unused Companion persona.

    Args:
        script: Validated conversation script.

    Returns:
        Speaker configs and a map from original aliases to alphanumeric aliases.

    Raises:
        ValueError: When the script declares more than two voices.
    """
    if len(script.voices) > 2:
        raise ValueError(
            f"ConversationScript has {len(script.voices)} voices; "
            "Cloud TTS multi-speaker synthesis supports at most 2."
        )

    speaker_configs: list[texttospeech.MultispeakerPrebuiltVoice] = []
    alias_map: dict[str, str] = {}
    for alias, voice in script.voices.items():
        clean_alias = re.sub(r"[^a-zA-Z0-9]", "", alias) or f"Speaker{len(alias_map) + 1}"
        alias_map[alias] = clean_alias
        speaker_configs.append(
            texttospeech.MultispeakerPrebuiltVoice(
                speaker_alias=clean_alias,
                speaker_id=extract_speaker_persona(voice.name),
            )
        )

    if len(speaker_configs) == 1:
        primary_persona = speaker_configs[0].speaker_id
        companion_persona = "Aoede" if primary_persona != "Aoede" else "Fenrir"
        speaker_configs.append(
            texttospeech.MultispeakerPrebuiltVoice(
                speaker_alias="Companion",
                speaker_id=companion_persona,
            )
        )

    return speaker_configs, alias_map


class TtsSynthesisService:
    """Synthesize conversation scripts to local WAV (no GCS upload)."""

    def __init__(
        self,
        tts_client: texttospeech.TextToSpeechClient,
        *,
        max_batch_chars: int = MAX_BATCH_CHARS,
    ) -> None:
        """Bind an EU TextToSpeechClient and optional batch size override."""
        self._tts_client = tts_client
        self._max_batch_chars = max_batch_chars

    def synthesize(
        self,
        script: ConversationScript,
        output_path: Path | str,
        *,
        should_cancel: Callable[[], bool] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> Path:
        """Synthesize a script with sequential ``synthesize_speech`` batches.

        Each batch is retried independently via GAPIC ``retry=``. PCM is stitched
        locally with ``pause_after_ms`` (or 300ms) between batches. ``should_cancel``
        is checked before each batch (not mid-RPC); if it returns true,
        ``SynthesisCancelled`` is raised and no WAV write occurs.

        Args:
            script: Validated conversation script.
            output_path: Local WAV destination.
            should_cancel: Optional cooperative cancel callback.
            on_progress: Optional ``(batch_index, batch_count)`` callback (1-based).

        Returns:
            Resolved local path of the written WAV file.

        Raises:
            SynthesisCancelled: When ``should_cancel`` returns true between batches.
        """
        dest = Path(output_path).resolve()
        speaker_configs, alias_map = speaker_voice_configs(script)
        turn_batches = batch_turns(script.turns, max_batch_chars=self._max_batch_chars)
        sample_rate = script.metadata.sample_rate_hertz

        voice_params = texttospeech.VoiceSelectionParams(
            language_code=script.metadata.language_code,
            model_name=GEMINI_TTS_MODEL,
            multi_speaker_voice_config=texttospeech.MultiSpeakerVoiceConfig(
                speaker_voice_configs=speaker_configs
            ),
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=sample_rate,
        )

        batch_count = len(turn_batches)
        logger.info(
            "Synthesizing '%s': %d turns -> %d batch(es) via %s",
            script.metadata.title,
            len(script.turns),
            batch_count,
            EU_TTS_ENDPOINT,
        )

        with tracer.start_as_current_span("synthesize_script") as span:
            span.set_attribute("conversation.title", script.metadata.title)
            span.set_attribute("conversation.turns", len(script.turns))
            span.set_attribute("conversation.batches", batch_count)

            combined = bytearray()
            for batch_idx, batch in enumerate(turn_batches, start=1):
                if should_cancel is not None and should_cancel():
                    raise SynthesisCancelled()
                if on_progress is not None:
                    on_progress(batch_idx, batch_count)

                batch_chars = sum(len(turn.text) for turn in batch)
                logger.info(
                    "Synthesizing batch %d/%d (%d turns, %d chars)",
                    batch_idx,
                    batch_count,
                    len(batch),
                    batch_chars,
                )
                markup_turns = [
                    texttospeech.MultiSpeakerMarkup.Turn(
                        speaker=alias_map.get(turn.speaker, turn.speaker),
                        text=turn.text,
                    )
                    for turn in batch
                ]
                with tracer.start_as_current_span("tts.batch") as batch_span:
                    batch_span.set_attribute("conversation.batch_index", batch_idx)
                    batch_span.set_attribute("conversation.batch_chars", batch_chars)
                    response = self._tts_client.synthesize_speech(
                        input=texttospeech.SynthesisInput(
                            multi_speaker_markup=texttospeech.MultiSpeakerMarkup(turns=markup_turns)
                        ),
                        voice=voice_params,
                        audio_config=audio_config,
                        retry=TTS_RETRY,
                        timeout=120.0,
                    )
                combined.extend(pcm_from_wav(response.audio_content))

                if batch_idx < batch_count:
                    pause_ms = batch[-1].pause_after_ms
                    if pause_ms is None:
                        pause_ms = DEFAULT_BATCH_PAUSE_MS
                    combined.extend(silence_pcm(sample_rate, pause_ms))

            if should_cancel is not None and should_cancel():
                raise SynthesisCancelled()

            write_wav(dest, bytes(combined), sample_rate)
            logger.info("Wrote %s (%d bytes)", dest, dest.stat().st_size)

        return dest
