"""Pure helpers for TTS batching, silence, and WAV I/O."""

from __future__ import annotations

import io
import wave
from collections.abc import Sequence
from pathlib import Path

from tts_audio_conversation.logic.models import DialogueTurn

MAX_BATCH_CHARS = 1500
DEFAULT_BATCH_PAUSE_MS = 300
LINEAR16_SAMPLE_WIDTH = 2


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
