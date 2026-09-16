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
from tts_audio_conversation.logic.exceptions import (
    JobNotFound,
    ScriptPayloadError,
    SynthesisCancelled,
    VoiceCatalogError,
)
from tts_audio_conversation.logic.jobs.models import JobRecord, JobStatus
from tts_audio_conversation.logic.models import (
    AudioEncoding,
    ConversationMetadata,
    ConversationScript,
    DialogueTurn,
    VoiceConfig,
)
from tts_audio_conversation.logic.results import (
    DownloadResult,
    ScriptUploadResult,
    ScriptValidationResult,
    TranslateScriptResult,
    VoiceInfo,
)
from tts_audio_conversation.logic.service import (
    AudioConversationService,
    create_audio_conversation_service,
    create_audio_conversation_service_from_adc,
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
    "AudioConversationService",
    "AudioEncoding",
    "ConversationMetadata",
    "ConversationScript",
    "ConversationTranslator",
    "DialogueTurn",
    "DownloadResult",
    "EU_TRANSLATE_ENDPOINT",
    "EU_TTS_ENDPOINT",
    "JobNotFound",
    "JobRecord",
    "JobStatus",
    "MAX_BATCH_CHARS",
    "ScriptPayloadError",
    "ScriptUploadResult",
    "ScriptValidationResult",
    "StorageService",
    "SynthesisCancelled",
    "TranslateScriptResult",
    "TtsSynthesisService",
    "VoiceCatalogError",
    "VoiceCatalogService",
    "VoiceConfig",
    "VoiceInfo",
    "assert_voices_in_catalog",
    "create_audio_conversation_service",
    "create_audio_conversation_service_from_adc",
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
