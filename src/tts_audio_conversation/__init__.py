"""Shared library, CLI, and FastMCP HTTP server for EU Chirp 3 HD conversations."""

from tts_audio_conversation.logic.auth import get_credentials_and_project, resolve_project_id
from tts_audio_conversation.logic.client import (
    EU_TTS_ENDPOINT,
    MAX_BATCH_CHARS,
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
from tts_audio_conversation.logic.storage import delete_file, download_file, upload_file
from tts_audio_conversation.logic.template import create_script_template
from tts_audio_conversation.logic.translator import EU_TRANSLATE_ENDPOINT, ConversationTranslator

__all__ = [
    "AudioEncoding",
    "DialogueTurn",
    "EU_TRANSLATE_ENDPOINT",
    "EU_TTS_ENDPOINT",
    "MAX_BATCH_CHARS",
    "ConversationMetadata",
    "ConversationScript",
    "ConversationTranslator",
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
