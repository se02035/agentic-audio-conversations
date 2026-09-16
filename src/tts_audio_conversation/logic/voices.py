"""EU Chirp 3 HD voice catalog checks."""

from __future__ import annotations

from collections.abc import Callable

from google.cloud import texttospeech

from tts_audio_conversation.logic.exceptions import VoiceCatalogError
from tts_audio_conversation.logic.models import ConversationScript
from tts_audio_conversation.logic.tts import (
    EU_TTS_ENDPOINT,
    speaker_voice_configs,
)


class VoiceCatalogService:
    """List and assert Chirp 3 HD voices via the EU ``list_voices`` RPC."""

    def __init__(self, tts_client: texttospeech.TextToSpeechClient) -> None:
        """Bind an EU-configured TextToSpeechClient."""
        self._tts_client = tts_client

    def list_voices(self, language_code: str | None = None) -> list[dict[str, str]]:
        """List Chirp 3 HD voices from the EU ``list_voices`` RPC."""
        return list_chirp3_voices(self._tts_client, language_code=language_code)

    def assert_in_catalog(
        self,
        script: ConversationScript,
        *,
        list_voices_fn: Callable[..., list[dict[str, str]]] | None = None,
    ) -> None:
        """Fail fast if script (and Companion) voices are missing from the catalog."""
        assert_voices_in_catalog(
            self._tts_client,
            script,
            list_voices_fn=list_voices_fn,
        )


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
    script: ConversationScript,
    *,
    list_voices_fn: Callable[..., list[dict[str, str]]] | None = None,
) -> None:
    """Fail fast if script (and Companion) voices are missing from EU ``list_voices``.

    Args:
        client: EU-configured TextToSpeechClient.
        script: Validated conversation script.
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


# Re-export for callers that imported persona helpers via the old client module.
__all__ = [
    "VoiceCatalogService",
    "assert_voices_in_catalog",
    "list_chirp3_voices",
]
