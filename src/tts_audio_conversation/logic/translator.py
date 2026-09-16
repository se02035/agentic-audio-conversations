"""EU Cloud Translation for dialogue scripts, with Chirp 3 voice remapping."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from google.api_core import exceptions, retry
from google.api_core.client_options import ClientOptions
from google.auth.credentials import Credentials
from google.cloud import translate_v3

from tts_audio_conversation.logic.models import (
    ConversationMetadata,
    ConversationScript,
    DialogueTurn,
    VoiceConfig,
)

logger = logging.getLogger(__name__)

EU_TRANSLATE_ENDPOINT = "translate-eu.googleapis.com"
EU_TRANSLATE_LOCATION = "europe-west1"
MAX_TRANSLATE_CHARS = 8000

TRANSLATE_RETRY = retry.Retry(
    predicate=retry.if_exception_type(
        exceptions.ResourceExhausted,
        exceptions.TooManyRequests,
        exceptions.ServiceUnavailable,
        exceptions.InternalServerError,
        exceptions.DeadlineExceeded,
    ),
    initial=1.0,
    maximum=60.0,
    multiplier=2.0,
    timeout=120.0,
)


class ConversationTranslator:
    """Translate turns via ``TranslationServiceClient`` on translate-eu."""

    def __init__(self, project_id: str, credentials: Credentials) -> None:
        """Initialize the EU Translation client.

        Args:
            project_id: GCP project ID.
            credentials: Application Default Credentials.
        """
        self.project_id = project_id
        self.credentials = credentials
        self.client = translate_v3.TranslationServiceClient(
            credentials=self.credentials,
            client_options=ClientOptions(api_endpoint=EU_TRANSLATE_ENDPOINT),
        )

    @property
    def parent_location(self) -> str:
        """EU regional parent resource for Cloud Translation v3."""
        return f"projects/{self.project_id}/locations/{EU_TRANSLATE_LOCATION}"

    def map_voice_name(self, current_voice_name: str, target_lang: str) -> str:
        """Swap the locale prefix of a Chirp 3 voice name.

        Example:
            ``en-US-Chirp3-HD-Fenrir`` + ``de-DE`` -> ``de-DE-Chirp3-HD-Fenrir``
        """
        return map_voice_name(current_voice_name, target_lang)

    def remap_script_voices(
        self, script: ConversationScript, target_language_code: str
    ) -> ConversationScript:
        """Return a copy of ``script`` with voices remapped to ``target_language_code``."""
        return remap_script_voices(script, target_language_code)

    def translate_script(
        self,
        script: ConversationScript,
        target_language_code: str,
        *,
        max_chars: int = MAX_TRANSLATE_CHARS,
    ) -> ConversationScript:
        """Translate all turns and remap voices to the target locale.

        Args:
            script: Source script.
            target_language_code: Target BCP-47 tag (e.g. ``de-DE``).
            max_chars: Maximum characters per ``translate_text`` RPC.

        Returns:
            New script with translated turns and remapped voices.
        """
        logger.info(
            "Translating '%s' (%d turns) from %s to %s via %s",
            script.metadata.title,
            script.turn_count,
            script.metadata.language_code,
            target_language_code,
            EU_TRANSLATE_ENDPOINT,
        )

        source_iso = script.metadata.language_code.split("-")[0].lower()
        target_iso = target_language_code.split("-")[0].lower()
        texts = [turn.text for turn in script.turns]
        translated_texts = self._translate_texts(
            texts,
            source_iso=source_iso,
            target_iso=target_iso,
            max_chars=max_chars,
        )

        new_turns = [
            DialogueTurn(
                speaker=orig.speaker,
                text=translated,
                pause_after_ms=orig.pause_after_ms,
            )
            for orig, translated in zip(script.turns, translated_texts, strict=True)
        ]
        remapped = remap_script_voices(script, target_language_code)
        new_metadata = ConversationMetadata(
            title=f"{script.metadata.title} ({target_language_code})",
            description=script.metadata.description,
            language_code=target_language_code,
            audio_encoding=script.metadata.audio_encoding,
            sample_rate_hertz=script.metadata.sample_rate_hertz,
        )
        return ConversationScript(metadata=new_metadata, voices=remapped.voices, turns=new_turns)

    def _translate_texts(
        self,
        texts: list[str],
        *,
        source_iso: str,
        target_iso: str,
        max_chars: int,
    ) -> list[str]:
        """Translate ``texts`` in character-capped RPCs, preserving order."""
        translated: list[str] = []
        for batch in batch_translate_texts(texts, max_chars):
            response = self.client.translate_text(
                request={
                    "parent": self.parent_location,
                    "contents": batch,
                    "mime_type": "text/plain",
                    "source_language_code": source_iso,
                    "target_language_code": target_iso,
                },
                retry=TRANSLATE_RETRY,
            )
            translated.extend(item.translated_text for item in response.translations)
        if len(translated) != len(texts):
            raise RuntimeError(
                f"Translation returned {len(translated)} texts for {len(texts)} turns."
            )
        return translated


def map_voice_name(current_voice_name: str, target_lang: str) -> str:
    """Swap the locale prefix of a Chirp 3 voice name."""
    match = re.match(r"^[a-zA-Z]{2}-[a-zA-Z]{2}-(.*)$", current_voice_name)
    if match:
        return f"{target_lang}-{match.group(1)}"
    return f"{target_lang}-{current_voice_name}"


def remap_script_voices(
    script: ConversationScript, target_language_code: str
) -> ConversationScript:
    """Return a copy of ``script`` with voices remapped to ``target_language_code``.

    Dialogue text is unchanged. Used to preflight Chirp 3 HD names before billed TTS.
    """
    new_voices = {
        alias: VoiceConfig(
            name=map_voice_name(voice.name, target_language_code),
            language_code=target_language_code,
        )
        for alias, voice in script.voices.items()
    }
    new_metadata = script.metadata.model_copy(update={"language_code": target_language_code})
    return ConversationScript(metadata=new_metadata, voices=new_voices, turns=script.turns)


def batch_translate_texts(texts: Sequence[str], max_chars: int) -> list[list[str]]:
    """Pack strings into batches that stay at or under ``max_chars`` when possible.

    A single string longer than ``max_chars`` is sent alone.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")
    batches: list[list[str]] = []
    current: list[str] = []
    current_chars = 0
    for text in texts:
        length = len(text)
        if current and current_chars + length > max_chars:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(text)
        current_chars += length
    if current:
        batches.append(current)
    return batches
