"""Starter 2-speaker YAML/JSON script templates."""

from __future__ import annotations

import logging

from tts_audio_conversation.logic.models import (
    AudioEncoding,
    ConversationMetadata,
    ConversationScript,
    DialogueTurn,
    VoiceConfig,
)

logger = logging.getLogger(__name__)

SAMPLE_TURNS_BY_LANG: dict[str, list[DialogueTurn]] = {
    "en": [
        DialogueTurn(
            speaker="host",
            text=(
                "Welcome to Tech Pulse Europe. I'm your host, "
                "and today we're exploring Cloud TTS in the EU."
            ),
            pause_after_ms=400,
        ),
        DialogueTurn(
            speaker="guest",
            text=(
                "Thanks for having me! It's amazing how lifelike "
                "the Chirp 3 generative voices sound."
            ),
            pause_after_ms=500,
        ),
        DialogueTurn(
            speaker="host",
            text=(
                "Absolutely. And speech synthesis runs through the EU regional Cloud TTS endpoint."
            ),
            pause_after_ms=300,
        ),
        DialogueTurn(
            speaker="guest",
            text=(
                "That's a huge win for privacy and sovereignty. "
                "Let's dig into the technical architecture."
            ),
            pause_after_ms=600,
        ),
    ],
    "de": [
        DialogueTurn(
            speaker="host",
            text=(
                "Willkommen bei Tech Puls Europa! "
                "Heute sprechen wir über moderne Sprachsynthese in der EU."
            ),
            pause_after_ms=400,
        ),
        DialogueTurn(
            speaker="guest",
            text=(
                "Hallo! Vielen Dank für die Einladung. "
                "Die Chirp 3 Stimmen klingen bemerkenswert natürlich."
            ),
            pause_after_ms=500,
        ),
        DialogueTurn(
            speaker="host",
            text=(
                "Ganz genau. Die Sprachsynthese läuft dabei über den "
                "regionalen EU-Endpunkt von Cloud TTS."
            ),
            pause_after_ms=300,
        ),
        DialogueTurn(
            speaker="guest",
            text=(
                "Ein entscheidender Vorteil für europäische Unternehmen. "
                "Lassen Sie uns die Details besprechen."
            ),
            pause_after_ms=600,
        ),
    ],
    "fr": [
        DialogueTurn(
            speaker="host",
            text=(
                "Bienvenue sur Tech Pulse Europe. "
                "Aujourd'hui, nous explorons la synthèse vocale dans l'UE."
            ),
            pause_after_ms=400,
        ),
        DialogueTurn(
            speaker="guest",
            text=(
                "Merci de m'accueillir ! "
                "Les voix génératives Chirp 3 sont d'un réalisme impressionnant."
            ),
            pause_after_ms=500,
        ),
        DialogueTurn(
            speaker="host",
            text=(
                "Absolument. Et la synthèse vocale utilise le point "
                "de terminaison régional Cloud TTS de l'UE."
            ),
            pause_after_ms=300,
        ),
    ],
}


def create_script_template(language_code: str = "en-US") -> ConversationScript:
    """Generate a starter 2-speaker script with native Chirp 3 HD voices.

    Sample dialogue exists for ``en``, ``de``, and ``fr`` language prefixes.
    Other locales keep the requested ``language_code`` and Chirp 3 HD voice
    names but reuse the English sample turns.

    Args:
        language_code: BCP-47 language tag (e.g. ``en-US``, ``de-DE``).

    Returns:
        Sample ``ConversationScript`` with independent turn copies.
    """
    lang_prefix = language_code.split("-")[0].lower()
    if lang_prefix not in SAMPLE_TURNS_BY_LANG:
        logger.warning(
            "SAMPLE_TURNS_BY_LANG has no %r sample; falling back to English for %s",
            lang_prefix,
            language_code,
        )
    source_turns = SAMPLE_TURNS_BY_LANG.get(lang_prefix, SAMPLE_TURNS_BY_LANG["en"])
    turns = [turn.model_copy() for turn in source_turns]
    return ConversationScript(
        metadata=ConversationMetadata(
            title=f"Sample Conversation ({language_code})",
            description="A 2-speaker automated conversation generated with Google Cloud TTS EU",
            language_code=language_code,
            audio_encoding=AudioEncoding.LINEAR16,
            sample_rate_hertz=24000,
        ),
        voices={
            "host": VoiceConfig(
                name=f"{language_code}-Chirp3-HD-Fenrir",
                language_code=language_code,
            ),
            "guest": VoiceConfig(
                name=f"{language_code}-Chirp3-HD-Aoede",
                language_code=language_code,
            ),
        },
        turns=turns,
    )
