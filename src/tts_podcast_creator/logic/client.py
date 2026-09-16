"""Batched multi-speaker synthesis via the official EU Cloud TTS client."""

from __future__ import annotations

import io
import logging
import re
import wave
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from google.api_core import exceptions, retry
from google.api_core.client_options import ClientOptions
from google.auth.credentials import Credentials
from google.cloud import texttospeech
from opentelemetry import trace

from tts_podcast_creator.logic.exceptions import SynthesisCancelled, VoiceCatalogError
from tts_podcast_creator.logic.models import DialogueTurn, PodcastScript
from tts_podcast_creator.logic.storage import upload_file

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

EU_TTS_ENDPOINT = "eu-texttospeech.googleapis.com"
GEMINI_TTS_MODEL = "gemini-2.5-flash-tts"
MAX_BATCH_CHARS = 1500
DEFAULT_BATCH_PAUSE_MS = 300
LINEAR16_SAMPLE_WIDTH = 2

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
    script: PodcastScript,
) -> tuple[list[texttospeech.MultispeakerPrebuiltVoice], dict[str, str]]:
    """Build MultiSpeakerVoiceConfig entries and an alias map.

    Cloud TTS requires at least two speaker definitions, so a one-speaker script
    gets an unused Companion persona.

    Args:
        script: Validated podcast script.

    Returns:
        Speaker configs and a map from original aliases to alphanumeric aliases.
    """
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


def split_long_text(text: str, max_chars: int) -> list[str]:
    """Split text that exceeds the per-request character cap on word boundaries."""
    stripped = text.strip()
    if len(stripped) <= max_chars:
        return [stripped]

    chunks: list[str] = []
    remaining = stripped
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        split_at = remaining.rfind(" ", 0, max_chars)
        if split_at <= 0:
            split_at = max_chars
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return [chunk for chunk in chunks if chunk]


def batch_turns(
    turns: Sequence[DialogueTurn],
    max_batch_chars: int = MAX_BATCH_CHARS,
) -> list[list[DialogueTurn]]:
    """Pack turns into character-capped batches; flush after ``pause_after_ms``.

    Args:
        turns: Ordered dialogue turns.
        max_batch_chars: Maximum characters per Cloud TTS request.

    Returns:
        Batches of turns to send as ``multi_speaker_markup``.
    """
    expanded: list[DialogueTurn] = []
    for turn in turns:
        parts = split_long_text(turn.text, max_batch_chars)
        for index, part in enumerate(parts):
            pause = turn.pause_after_ms if index == len(parts) - 1 else None
            expanded.append(DialogueTurn(speaker=turn.speaker, text=part, pause_after_ms=pause))

    batches: list[list[DialogueTurn]] = []
    current: list[DialogueTurn] = []
    current_chars = 0
    for turn in expanded:
        turn_len = len(turn.text)
        if current and current_chars + turn_len > max_batch_chars:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(turn)
        current_chars += turn_len
        if turn.pause_after_ms is not None:
            batches.append(current)
            current = []
            current_chars = 0
    if current:
        batches.append(current)
    return batches


def silence_pcm(sample_rate_hertz: int, pause_ms: int) -> bytes:
    """Return 16-bit mono LINEAR16 silence.

    Args:
        sample_rate_hertz: Sample rate of the surrounding audio.
        pause_ms: Silence duration in milliseconds.

    Returns:
        Raw PCM bytes.
    """
    if pause_ms <= 0:
        return b""
    n_samples = int(sample_rate_hertz * pause_ms / 1000)
    return b"\x00" * (n_samples * LINEAR16_SAMPLE_WIDTH)


def pcm_from_wav(audio_content: bytes) -> bytes:
    """Extract PCM frames from a WAV payload returned by Cloud TTS."""
    with wave.open(io.BytesIO(audio_content), "rb") as wav_in:
        return wav_in.readframes(wav_in.getnframes())


def write_wav(path: Path, frames: bytes, sample_rate_hertz: int) -> None:
    """Write mono 16-bit LINEAR16 PCM as a WAV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_out:
        wav_out.setnchannels(1)
        wav_out.setsampwidth(LINEAR16_SAMPLE_WIDTH)
        wav_out.setframerate(sample_rate_hertz)
        wav_out.writeframes(frames)


def list_chirp3_voices(
    client: texttospeech.TextToSpeechClient,
    language_code: str | None = None,
) -> list[dict[str, str]]:
    """List Chirp 3 HD voices from the EU ``list_voices`` RPC.

    Args:
        client: EU-configured TextToSpeechClient.
        language_code: Optional BCP-47 filter (e.g. ``de-DE``).

    Returns:
        Voice metadata dictionaries with name, language_code, and gender.
    """
    response = client.list_voices(language_code=language_code or "")
    results: list[dict[str, str]] = []
    norm_filter = language_code.lower().replace("_", "-") if language_code else None
    for voice in response.voices:
        if "Chirp3-HD" not in voice.name:
            continue
        langs = list(voice.language_codes) or [""]
        if norm_filter and not any(lang.lower().startswith(norm_filter) for lang in langs):
            continue
        gender = texttospeech.SsmlVoiceGender(voice.ssml_gender).name
        results.append(
            {
                "name": voice.name,
                "language_code": langs[0],
                "gender": gender,
            }
        )
    return results


def assert_voices_in_catalog(
    client: texttospeech.TextToSpeechClient,
    script: PodcastScript,
    *,
    list_voices_fn: Callable[..., list[dict[str, str]]] | None = None,
) -> None:
    """Fail fast if script (and Companion) voices are missing from EU ``list_voices``.

    Args:
        client: EU-configured TextToSpeechClient.
        script: Validated podcast script.
        list_voices_fn: Optional override used by tests.

    Raises:
        VoiceCatalogError: When a required Chirp 3 HD name is absent, or listing fails.
    """
    lister = list_voices_fn or list_chirp3_voices
    lang = script.metadata.language_code
    try:
        catalog = lister(client, language_code=lang)
    except VoiceCatalogError:
        raise
    except Exception as exc:
        raise VoiceCatalogError(
            f"Failed to list Chirp 3 HD voices for {lang} via {EU_TTS_ENDPOINT}: {exc}"
        ) from exc
    names = {item["name"] for item in catalog}
    required = [voice.name for voice in script.voices.values()]
    if len(script.voices) == 1:
        configs, _alias_map = speaker_voice_configs(script)
        companion = next(cfg for cfg in configs if cfg.speaker_alias == "Companion")
        required.append(f"{lang}-Chirp3-HD-{companion.speaker_id}")
    missing = [name for name in required if name not in names]
    if missing:
        raise VoiceCatalogError(
            f"Voice(s) not found in EU Chirp 3 HD catalog for {lang}: {', '.join(missing)}"
        )


def synthesize_script(
    client: texttospeech.TextToSpeechClient,
    script: PodcastScript,
    output_path: Path | str,
    *,
    output_gcs_uri: str | None = None,
    gcs_client: Any | None = None,
    max_batch_chars: int = MAX_BATCH_CHARS,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Synthesize a script with sequential ``synthesize_speech`` batches.

    Each batch is retried independently via GAPIC ``retry=``. PCM is stitched
    locally with ``pause_after_ms`` (or 300ms) between batches. ``should_cancel``
    is checked before each batch (not mid-RPC); if it returns true,
    ``SynthesisCancelled`` is raised and no WAV/GCS write occurs.

    Args:
        client: EU-configured ``TextToSpeechClient``.
        script: Validated podcast script.
        output_path: Local WAV destination.
        output_gcs_uri: Optional ``gs://`` upload destination.
        gcs_client: Official Storage client used when ``output_gcs_uri`` is set.
        max_batch_chars: Per-request character cap.
        should_cancel: Optional cooperative cancel callback.
        on_progress: Optional ``(batch_index, batch_count)`` callback (1-based).

    Returns:
        Resolved local path of the written WAV file.

    Raises:
        SynthesisCancelled: When ``should_cancel`` returns true between batches.
    """
    dest = Path(output_path).resolve()
    speaker_configs, alias_map = speaker_voice_configs(script)
    turn_batches = batch_turns(script.turns, max_batch_chars=max_batch_chars)
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
        span.set_attribute("podcast.title", script.metadata.title)
        span.set_attribute("podcast.turns", len(script.turns))
        span.set_attribute("podcast.batches", batch_count)

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
                batch_span.set_attribute("podcast.batch_index", batch_idx)
                batch_span.set_attribute("podcast.batch_chars", batch_chars)
                response = client.synthesize_speech(
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

        if output_gcs_uri:
            if should_cancel is not None and should_cancel():
                raise SynthesisCancelled()
            if gcs_client is None:
                raise ValueError("gcs_client is required when output_gcs_uri is set.")
            with tracer.start_as_current_span("gcs.upload"):
                upload_file(gcs_client, output_gcs_uri, dest)
            logger.info("Uploaded %s", output_gcs_uri)

    return dest
