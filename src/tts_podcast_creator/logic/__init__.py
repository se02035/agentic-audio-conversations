"""Shared synthesis, translation, storage, and settings."""

from tts_podcast_creator.logic.auth import get_credentials_and_project, resolve_project_id
from tts_podcast_creator.logic.client import (
    EU_TTS_ENDPOINT,
    MAX_BATCH_CHARS,
    assert_voices_in_catalog,
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
from tts_podcast_creator.logic.storage import (
    delete_file,
    download_file,
    require_eu_bucket,
    upload_bytes,
    upload_file,
)
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
    "assert_voices_in_catalog",
    "create_script_template",
    "delete_file",
    "download_file",
    "eu_tts_client",
    "get_credentials_and_project",
    "list_chirp3_voices",
    "require_eu_bucket",
    "resolve_project_id",
    "synthesize_script",
    "upload_bytes",
    "upload_file",
]
