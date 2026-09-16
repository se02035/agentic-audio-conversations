"""Shared library, CLI, and FastMCP HTTP server for EU Chirp 3 HD podcasts."""

from tts_podcast_creator.logic.auth import get_credentials_and_project, resolve_project_id
from tts_podcast_creator.logic.client import (
    EU_TTS_ENDPOINT,
    MAX_BATCH_CHARS,
    eu_tts_client,
    list_chirp3_voices,
    synthesize_script,
)
from tts_podcast_creator.logic.models import (
    AudioEncoding,
    DialogueTurn,
    PodcastMetadata,
    PodcastScript,
    VoiceConfig,
)
from tts_podcast_creator.logic.storage import delete_file, download_file, upload_file
from tts_podcast_creator.logic.template import create_script_template
from tts_podcast_creator.logic.translator import EU_TRANSLATE_ENDPOINT, PodcastTranslator

__all__ = [
    "AudioEncoding",
    "DialogueTurn",
    "EU_TRANSLATE_ENDPOINT",
    "EU_TTS_ENDPOINT",
    "MAX_BATCH_CHARS",
    "PodcastMetadata",
    "PodcastScript",
    "PodcastTranslator",
    "VoiceConfig",
    "create_script_template",
    "delete_file",
    "download_file",
    "eu_tts_client",
    "get_credentials_and_project",
    "list_chirp3_voices",
    "resolve_project_id",
    "synthesize_script",
    "upload_file",
]
