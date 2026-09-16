"""Shared synthesis, translation, storage, and settings."""

from tts_audio_conversation.logic.auth import get_credentials_and_project, resolve_project_id
from tts_audio_conversation.logic.client import (
    EU_TTS_ENDPOINT,
    MAX_BATCH_CHARS,
    assert_voices_in_catalog,
    eu_tts_client,
    list_chirp3_voices,
    synthesize_script,
)
from tts_audio_conversation.logic.models import (
    AudioEncoding,
    ConversationMetadata,
    ConversationScript,
    DialogueTurn,
    VoiceConfig,
)
from tts_audio_conversation.logic.storage import (
    StorageService,
    delete_file,
    download_file,
    upload_bytes,
    upload_file,
)
from tts_audio_conversation.logic.template import create_script_template
from tts_audio_conversation.logic.translator import EU_TRANSLATE_ENDPOINT, ConversationTranslator
from tts_audio_conversation.logic.tts import TtsSynthesisService
from tts_audio_conversation.logic.voices import VoiceCatalogService

__all__ = [
    "AudioEncoding",
    "ConversationMetadata",
    "ConversationScript",
    "ConversationTranslator",
    "DialogueTurn",
    "EU_TRANSLATE_ENDPOINT",
    "EU_TTS_ENDPOINT",
    "MAX_BATCH_CHARS",
    "StorageService",
    "TtsSynthesisService",
    "VoiceCatalogService",
    "VoiceConfig",
    "assert_voices_in_catalog",
    "create_script_template",
    "delete_file",
    "download_file",
    "eu_tts_client",
    "get_credentials_and_project",
    "list_chirp3_voices",
    "resolve_project_id",
    "synthesize_script",
    "upload_bytes",
    "upload_file",
]
