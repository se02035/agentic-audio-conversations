"""Public facade for EU Chirp 3 HD conversation audio creation.

Typical usage::

    service = create_audio_conversation_service(settings, credentials, project_id)
    uploaded = service.upload_script(Path("episode.yaml"))
    job = await service.create_audio(uploaded.script_uri)
"""

from __future__ import annotations

import uuid
from pathlib import Path

from google.auth.credentials import Credentials

from tts_audio_conversation.logic.exceptions import ScriptPayloadError
from tts_audio_conversation.logic.jobs.manager import JobManager
from tts_audio_conversation.logic.jobs.models import JobRecord
from tts_audio_conversation.logic.models import ConversationScript
from tts_audio_conversation.logic.results import (
    DownloadResult,
    ScriptUploadResult,
    ScriptValidationResult,
    TranslateScriptResult,
    VoiceInfo,
)
from tts_audio_conversation.logic.settings import Settings
from tts_audio_conversation.logic.storage import StorageService
from tts_audio_conversation.logic.translator import ConversationTranslator
from tts_audio_conversation.logic.voices import VoiceCatalogService


def _language_matches(current: str, target: str) -> bool:
    """Return True when ``current`` is already the translation target ``target``."""
    current_lang = current.lower()
    target_lang = target.lower()
    return current_lang == target_lang or current_lang.startswith(f"{target_lang}-")


def _require_gs_uri(uri: str) -> str:
    """Return ``uri`` when it is a ``gs://`` object URI."""
    text = uri.strip()
    if not text.startswith("gs://") or text.count("/") < 3:
        raise ScriptPayloadError(f"Expected a gs://bucket/object URI, got '{uri}'.")
    return text


class AudioConversationService:
    """Injectable library entry for upload → translate → job-based synthesis.

    All processing methods that accept a script take a ``gs://`` URI after
    :meth:`upload_script`. Audio creation is async-job only; call
    :meth:`get_job` / :meth:`cancel_job` to poll or cancel.
    """

    def __init__(
        self,
        settings: Settings,
        jobs: JobManager,
        voices: VoiceCatalogService,
        translator: ConversationTranslator,
        storage: StorageService,
    ) -> None:
        """Bind collaborators. Adapters resolve credentials before construction.

        Args:
            settings: Environment-backed configuration (bucket, caps, MCP bind).
            jobs: In-process job manager for create/get/cancel (owns TTS pipeline work).
            voices: EU Chirp 3 HD catalog.
            translator: EU Cloud Translation client wrapper.
            storage: GCS object I/O.
        """
        self._settings = settings
        self._jobs = jobs
        self._voices = voices
        self._translator = translator
        self._storage = storage

    def upload_script(
        self,
        source: Path | str,
        *,
        script_id: str | None = None,
    ) -> ScriptUploadResult:
        """Upload a local file or inline YAML/JSON payload to staging GCS.

        Always writes ``…/scripts/{script_id}/script.yaml`` (normalized YAML).

        Args:
            source: Local ``Path`` or inline YAML/JSON string.
            script_id: Optional folder id; a UUID is generated when omitted.

        Returns:
            ``script_uri`` and ``script_id``.

        Raises:
            ScriptPayloadError: When the payload is empty, oversized, or invalid.
            ValueError: When the staging bucket is not configured.
        """
        self._settings.require_gcs_bucket()
        if isinstance(source, Path):
            payload = source.read_text(encoding="utf-8")
        else:
            payload = source
        script = ConversationScript.from_payload(
            payload, max_bytes=self._settings.audio_conversation_max_script_bytes
        )
        sid = script_id or str(uuid.uuid4())
        script_uri = f"{self._settings.script_prefix_uri(sid)}/script.yaml"
        self._storage.upload_bytes(
            script_uri,
            script.to_yaml().encode("utf-8"),
            content_type="application/x-yaml",
        )
        return ScriptUploadResult(script_uri=script_uri, script_id=sid)

    def validate_script(self, script_uri: str) -> ScriptValidationResult:
        """Load and validate a script from a ``gs://`` URI (no ``list_voices``).

        Never raises for invalid input — returns ``valid=False`` and ``error``.

        Args:
            script_uri: ``gs://`` URI of an uploaded script.

        Returns:
            Soft validation summary including the input URI.
        """
        try:
            uri = _require_gs_uri(script_uri)
            raw = self._storage.download_bytes(uri)
            if raw is None:
                return ScriptValidationResult(
                    valid=False,
                    script_uri=uri,
                    error=f"Object not found: {uri}",
                )
            script = ConversationScript.from_payload(
                raw.decode("utf-8"),
                max_bytes=self._settings.audio_conversation_max_script_bytes,
            )
        except Exception as exc:
            return ScriptValidationResult(
                valid=False,
                script_uri=script_uri.strip(),
                error=str(exc),
            )
        return ScriptValidationResult(
            valid=True,
            title=script.metadata.title,
            language_code=script.metadata.language_code,
            speakers=list(script.voices),
            turn_count=script.turn_count,
            total_characters=script.total_character_count,
            script_uri=uri,
            error=None,
        )

    def list_voices(self, language_code: str | None = None) -> list[VoiceInfo]:
        """List Chirp 3 HD voices via the EU ``list_voices`` RPC.

        Args:
            language_code: Optional BCP-47 filter (e.g. ``de-DE``).

        Returns:
            Voice metadata rows.
        """
        rows = self._voices.list_voices(language_code=language_code)
        return [
            VoiceInfo(
                name=row["name"],
                language_code=row["language_code"],
                gender=row["gender"],
            )
            for row in rows
        ]

    def translate_script(
        self,
        script_uri: str,
        target_language: str,
    ) -> TranslateScriptResult:
        """Translate a GCS script via translate-eu and write a sibling object.

        Skips the RPC when the script is already in ``target_language``
        (``skipped=True``, ``output_uri`` equals the input).

        Args:
            script_uri: ``gs://`` URI of the source script.
            target_language: BCP-47 target (e.g. ``de-DE``).

        Returns:
            ``output_uri`` to pass to :meth:`create_audio`.

        Raises:
            ScriptPayloadError: When the URI is missing or the payload is invalid.
        """
        uri = _require_gs_uri(script_uri)
        script = self._load_script(uri)
        if _language_matches(script.metadata.language_code, target_language):
            return TranslateScriptResult(
                output_uri=uri,
                language_code=script.metadata.language_code,
                title=script.metadata.title,
                skipped=True,
            )
        translated = self._translator.translate_script(script, target_language)
        parent = uri.rsplit("/", 1)[0]
        lang_slug = target_language.replace("_", "-")
        output_uri = f"{parent}/script.{lang_slug}.yaml"
        self._storage.upload_bytes(
            output_uri,
            translated.to_yaml().encode("utf-8"),
            content_type="application/x-yaml",
        )
        return TranslateScriptResult(
            output_uri=output_uri,
            language_code=translated.metadata.language_code,
            title=translated.metadata.title,
            skipped=False,
        )

    async def create_audio(self, script_uri: str) -> JobRecord:
        """Start a background job from an uploaded script ``gs://`` URI.

        Preflight ``list_voices``, persist queued ``status.json``, and return
        before ``synthesize_speech``. Does not translate — call
        :meth:`translate_script` first when needed.

        Args:
            script_uri: ``gs://`` URI of the script to synthesize.

        Returns:
            Queued (or already running) job snapshot.

        Raises:
            ScriptPayloadError: When the URI or payload is invalid.
            VoiceCatalogError: When a required voice is missing from the catalog.
            ValueError: When the staging bucket is not configured.
        """
        return await self._jobs.start(_require_gs_uri(script_uri))

    async def get_job(self, job_id: str) -> JobRecord:
        """Poll job status (in-memory, else GCS ``status.json`` after restart).

        Args:
            job_id: Id returned by :meth:`create_audio`.

        Returns:
            Current job snapshot.

        Raises:
            JobNotFound: When the id is unknown to this process and GCS.
        """
        return await self._jobs.get_status(job_id)

    async def cancel_job(self, job_id: str) -> JobRecord:
        """Request cooperative cancel for a queued or running job.

        Args:
            job_id: Id returned by :meth:`create_audio`.

        Returns:
            Updated job snapshot (often ``cancelled``).

        Raises:
            JobNotFound: When the id is unknown to this process and GCS.
        """
        return await self._jobs.cancel(job_id)

    def download(self, gcs_uri: str, destination: Path) -> DownloadResult:
        """Download a ``gs://`` object to a local path.

        Args:
            gcs_uri: Source object URI.
            destination: Local file path.

        Returns:
            Local path and source URI.

        Raises:
            ScriptPayloadError: When ``gcs_uri`` is not a ``gs://`` URI.
        """
        uri = _require_gs_uri(gcs_uri)
        dest = Path(destination)
        self._storage.download_file(uri, dest)
        return DownloadResult(local_path=dest, gcs_uri=uri)

    async def close(self) -> None:
        """Cancel in-flight jobs, await synthesis tasks, then shut down executors."""
        await self._jobs.close()

    def _load_script(self, script_uri: str) -> ConversationScript:
        raw = self._storage.download_bytes(script_uri)
        if raw is None:
            raise ScriptPayloadError(f"Object not found: {script_uri}")
        return ConversationScript.from_payload(
            raw.decode("utf-8"),
            max_bytes=self._settings.audio_conversation_max_script_bytes,
        )


def create_audio_conversation_service(
    settings: Settings,
    credentials: Credentials,
    project_id: str,
) -> AudioConversationService:
    """Wire leaf services and job manager (no ADC lookup inside).

    Args:
        settings: Loaded settings (bucket required for upload/jobs).
        credentials: Caller-supplied Google credentials.
        project_id: GCP project id.

    Returns:
        Fully wired :class:`AudioConversationService`.
    """
    from google.cloud import storage  # type: ignore[attr-defined]

    from tts_audio_conversation.logic.client import eu_tts_client
    from tts_audio_conversation.logic.jobs.job import CloudHandles
    from tts_audio_conversation.logic.tts import TtsSynthesisService

    tts_client = eu_tts_client(credentials)
    gcs_client = storage.Client(project=project_id, credentials=credentials)
    tts = TtsSynthesisService(tts_client)
    storage_svc = StorageService(gcs_client)
    voices = VoiceCatalogService(tts_client)
    translator = ConversationTranslator(project_id=project_id, credentials=credentials)

    def clients_factory() -> CloudHandles:
        return CloudHandles(
            tts_client=tts_client,
            gcs_client=gcs_client,
        )

    def synthesize_fn(
        _client: object,
        script: ConversationScript,
        output_path: Path,
        **kwargs: object,
    ) -> Path:
        from typing import Any, cast

        return tts.synthesize(script, Path(output_path), **cast(Any, kwargs))

    jobs = JobManager(
        settings,
        clients_factory=clients_factory,
        synthesize_fn=synthesize_fn,
        upload_bytes_fn=lambda _c, uri, data, ctype: storage_svc.upload_bytes(
            uri, data, content_type=ctype
        ),
        download_bytes_fn=lambda _c, uri: storage_svc.download_bytes(uri),
        upload_file_fn=lambda _c, uri, path: storage_svc.upload_file(uri, path),
        delete_file_fn=lambda _c, uri: storage_svc.delete_file(uri),
        voice_catalog_fn=lambda script, _handles: voices.assert_in_catalog(script),
    )
    return AudioConversationService(
        settings=settings,
        jobs=jobs,
        voices=voices,
        translator=translator,
        storage=storage_svc,
    )


def create_audio_conversation_service_from_adc(
    settings: Settings,
    *,
    project: str | None = None,
) -> AudioConversationService:
    """Resolve ADC via auth helpers, then call :func:`create_audio_conversation_service`.

    Args:
        settings: Loaded settings.
        project: Optional project override (CLI ``--project``).

    Returns:
        Fully wired service.
    """
    from tts_audio_conversation.logic.auth import get_credentials_and_project

    credentials, project_id = get_credentials_and_project(project)
    return create_audio_conversation_service(settings, credentials, project_id)
