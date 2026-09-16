"""Pure helpers for TTS batching, silence, and WAV I/O."""

from __future__ import annotations

import io
import wave
from collections.abc import Sequence
from pathlib import Path

from tts_audio_conversation.logic.models import DialogueTurn

MAX_BATCH_CHARS = 1500
# Gemini TTS MultiSpeakerMarkup field limit (UTF-8 bytes, not characters).
MAX_BATCH_UTF8_BYTES = 4000
# Conservative protobuf framing for MultiSpeakerMarkup and each Turn.
_MARKUP_FRAMING_BYTES = 8
_TURN_FRAMING_BYTES = 8
DEFAULT_BATCH_PAUSE_MS = 300
LINEAR16_SAMPLE_WIDTH = 2


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def turn_markup_utf8_bytes(speaker: str, text: str) -> int:
    """Estimate UTF-8 size of one MultiSpeakerMarkup.Turn including framing."""
    return _TURN_FRAMING_BYTES + _utf8_len(speaker) + _utf8_len(text)


def markup_utf8_bytes(turns: Sequence[DialogueTurn]) -> int:
    """Estimate UTF-8 size of a MultiSpeakerMarkup payload including overhead."""
    return _MARKUP_FRAMING_BYTES + sum(
        turn_markup_utf8_bytes(turn.speaker, turn.text) for turn in turns
    )


def split_long_text(
    text: str,
    max_chars: int,
    *,
    max_utf8_bytes: int | None = None,
) -> list[str]:
    """Split text that exceeds per-request character and/or UTF-8 byte caps.

    Args:
        text: Dialogue text to split on word boundaries when possible.
        max_chars: Maximum Unicode characters per chunk (must be >= 1).
        max_utf8_bytes: Optional maximum UTF-8 bytes per chunk.

    Returns:
        Non-empty text chunks that each fit the active limits.

    Raises:
        ValueError: When ``max_chars`` (or ``max_utf8_bytes``) is less than 1,
            or when the UTF-8 budget cannot encode the next character.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")
    if max_utf8_bytes is not None and max_utf8_bytes < 1:
        raise ValueError("max_utf8_bytes must be >= 1")

    stripped = text.strip()
    if _chunk_fits(stripped, max_chars, max_utf8_bytes):
        return [stripped]

    chunks: list[str] = []
    remaining = stripped
    while remaining:
        if _chunk_fits(remaining, max_chars, max_utf8_bytes):
            chunks.append(remaining)
            break
        split_at = _split_index(remaining, max_chars, max_utf8_bytes)
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return [chunk for chunk in chunks if chunk]


def _chunk_fits(text: str, max_chars: int, max_utf8_bytes: int | None) -> bool:
    if len(text) > max_chars:
        return False
    if max_utf8_bytes is not None and _utf8_len(text) > max_utf8_bytes:
        return False
    return True


def _split_index(text: str, max_chars: int, max_utf8_bytes: int | None) -> int:
    """Return a cut index that respects both character and UTF-8 byte caps."""
    limit = min(max_chars, len(text))
    if max_utf8_bytes is not None:
        while limit > 0 and _utf8_len(text[:limit]) > max_utf8_bytes:
            limit -= 1
    if limit <= 0:
        raise ValueError("UTF-8 byte budget cannot encode the next character")
    split_at = text.rfind(" ", 0, limit)
    if split_at <= 0:
        return limit
    return split_at


def batch_turns(
    turns: Sequence[DialogueTurn],
    max_batch_chars: int = MAX_BATCH_CHARS,
    *,
    max_batch_utf8_bytes: int = MAX_BATCH_UTF8_BYTES,
) -> list[list[DialogueTurn]]:
    """Pack turns into character- and UTF-8-capped batches; flush after pause.

    Enforces both ``max_batch_chars`` and Gemini's ``max_batch_utf8_bytes``
    MultiSpeakerMarkup limit. UTF-8 accounting includes speaker/text bytes and
    conservative request framing overhead.

    Args:
        turns: Ordered dialogue turns.
        max_batch_chars: Maximum characters per Cloud TTS request.
        max_batch_utf8_bytes: Maximum UTF-8 bytes for MultiSpeakerMarkup.

    Returns:
        Batches of turns to send as ``multi_speaker_markup``.

    Raises:
        ValueError: When a limit is less than 1, or a speaker alias alone
            exhausts the UTF-8 budget.
    """
    if max_batch_chars < 1:
        raise ValueError("max_batch_chars must be >= 1")
    if max_batch_utf8_bytes < 1:
        raise ValueError("max_batch_utf8_bytes must be >= 1")

    expanded: list[DialogueTurn] = []
    for turn in turns:
        text_byte_budget = (
            max_batch_utf8_bytes
            - _MARKUP_FRAMING_BYTES
            - _TURN_FRAMING_BYTES
            - _utf8_len(turn.speaker)
        )
        if text_byte_budget < 1:
            raise ValueError(
                f"Speaker alias '{turn.speaker}' leaves no UTF-8 budget for dialogue text."
            )
        parts = split_long_text(
            turn.text,
            max_batch_chars,
            max_utf8_bytes=text_byte_budget,
        )
        for index, part in enumerate(parts):
            pause = turn.pause_after_ms if index == len(parts) - 1 else None
            expanded.append(DialogueTurn(speaker=turn.speaker, text=part, pause_after_ms=pause))

    batches: list[list[DialogueTurn]] = []
    current: list[DialogueTurn] = []
    current_chars = 0
    for turn in expanded:
        turn_len = len(turn.text)
        prospective = [*current, turn]
        if current and (
            current_chars + turn_len > max_batch_chars
            or markup_utf8_bytes(prospective) > max_batch_utf8_bytes
        ):
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
