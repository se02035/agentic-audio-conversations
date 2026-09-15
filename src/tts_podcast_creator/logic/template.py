"""Starter 2-speaker YAML/JSON script templates."""

from __future__ import annotations

from tts_podcast_creator.logic.models import (
    AudioEncoding,
    DialogueTurn,
    PodcastMetadata,
    PodcastScript,
    VoiceConfig,
)

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
                "Absolutely. And with data residency locked to "
                "the European Union, compliance is guaranteed."
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
                "Ganz genau. Besonders wichtig ist dabei die "
                "vollständige DSGVO- und Datenresidenz-Konformität in der EU."
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
                "Absolument. Et le traitement des données reste "
                "strictement confiné au sein de l'Union européenne."
            ),
            pause_after_ms=300,
        ),
    ],
}


def create_script_template(language_code: str = "en-US") -> PodcastScript:
    """Generate a starter 2-speaker script with native Chirp 3 HD voices.

    Args:
        language_code: BCP-47 language tag (e.g. ``en-US``, ``de-DE``).

    Returns:
        Sample ``PodcastScript``.
    """
    lang_prefix = language_code.split("-")[0].lower()
    turns = SAMPLE_TURNS_BY_LANG.get(lang_prefix, SAMPLE_TURNS_BY_LANG["en"])
    return PodcastScript(
        metadata=PodcastMetadata(
            title=f"Sample Podcast ({language_code})",
            description="A 2-speaker automated podcast generated with Google Cloud TTS EU",
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
