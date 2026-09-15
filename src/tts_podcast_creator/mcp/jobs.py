"""In-process podcast jobs: validate immediately, synthesize in the background."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from pydantic import BaseModel, Field

from tts_podcast_creator.logic.exceptions import JobNotFound, ScriptPayloadError, SynthesisCancelled
from tts_podcast_creator.logic.models import PodcastScript
from tts_podcast_creator.logic.settings import Settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

_STALE_ERROR = (
    "Job heartbeat expired; treating as failed (process likely crashed). Start a new job."
)


class JobStatus(StrEnum):
    """Lifecycle of one podcast generation job."""

    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class JobRecord(BaseModel):
    """Public job snapshot returned by MCP tools and stored as ``status.json``."""

    job_id: str
    status: JobStatus
    audio_uri: str
    status_uri: str
    error: str | None = None
    progress: str | None = Field(default=None, description="Optional batch i/n hint.")
    updated_at: datetime | None = Field(
        default=None, description="UTC heartbeat written on each persist."
    )


@dataclass
class CloudHandles:
    """ADC-backed SDK clients created inside the worker, never in start_podcast."""

    credentials: Any
    project_id: str
    tts_client: Any
    gcs_client: Any


ClientsFactory = Callable[[], CloudHandles]
SynthesizeFn = Callable[..., Path]
TranslateFn = Callable[[PodcastScript, str, CloudHandles], PodcastScript]
UploadBytesFn = Callable[[Any, str, bytes, str], None]
DownloadBytesFn = Callable[[Any, str], bytes | None]
UploadFileFn = Callable[[Any, str, Path], None]
DeleteFileFn = Callable[[Any, str], None]
VoiceCatalogFn = Callable[[PodcastScript, CloudHandles], None]


def utc_now() -> datetime:
    """Return timezone-aware UTC now."""
    return datetime.now(UTC)


def as_utc(moment: datetime | None) -> datetime | None:
    """Normalize a datetime to UTC."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


class JobManager:
    """Concurrent podcast jobs with cooperative cancel between TTS batches.

    ``start`` never waits on TTS. It may write ``status.json`` and call EU
    ``list_voices``. Extra jobs beyond the concurrency cap stay ``queued``.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        clients_factory: ClientsFactory | None = None,
        synthesize_fn: SynthesizeFn | None = None,
        translate_fn: TranslateFn | None = None,
        upload_bytes_fn: UploadBytesFn | None = None,
        download_bytes_fn: DownloadBytesFn | None = None,
        upload_file_fn: UploadFileFn | None = None,
        delete_file_fn: DeleteFileFn | None = None,
        voice_catalog_fn: VoiceCatalogFn | None = None,
    ) -> None:
        """Create a manager. Production callers omit the injectable callables."""
        self.settings = settings
        self._clients_factory = clients_factory or default_clients_factory
        self._synthesize = synthesize_fn or _default_synthesize
        self._translate = translate_fn
        self._upload_bytes = upload_bytes_fn or _default_upload_bytes
        self._download_bytes = download_bytes_fn or _default_download_bytes
        self._upload_file = upload_file_fn or _default_upload_file
        self._delete_file = delete_file_fn or _default_delete_file
        self._voice_catalog = voice_catalog_fn
        self._jobs: dict[str, JobRecord] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = threading.Lock()
        self._semaphore = asyncio.Semaphore(settings.podcast_max_concurrent_jobs)
        self._executor = ThreadPoolExecutor(
            max_workers=max(settings.podcast_max_concurrent_jobs, 4),
            thread_name_prefix="podcast-tts",
        )

    def parse_script(self, payload: str) -> PodcastScript:
        """Validate a YAML/JSON payload against the script schema and size cap."""
        return PodcastScript.from_payload(payload, max_bytes=self.settings.podcast_max_script_bytes)

    def _preflight_voices(self, script: PodcastScript, translate_to: str | None) -> None:
        """Confirm Chirp 3 HD names exist in the EU catalog before enqueueing."""
        handles = self._clients_factory()
        checker = self._voice_catalog or _default_voice_catalog
        checker(script, handles)
        if not translate_to:
            return
        current_lang = script.metadata.language_code.lower()
        target_lang = translate_to.lower()
        if current_lang == target_lang or current_lang.startswith(f"{target_lang}-"):
            return
        from tts_podcast_creator.logic.translator import remap_script_voices

        preview = remap_script_voices(script, translate_to)
        checker(preview, handles)

    async def start(self, script_payload: str, translate_to: str | None = None) -> JobRecord:
        """Validate, preflight voices, persist queued status, schedule work, return.

        Does not call ``synthesize_speech``. Writes ``status.json`` before return.
        """
        self._prune()
        script = self.parse_script(script_payload)
        self.settings.require_gcs_bucket()
        self._preflight_voices(script, translate_to)
        job_id = str(uuid.uuid4())
        prefix = self.settings.job_prefix_uri(job_id)
        record = JobRecord(
            job_id=job_id,
            status=JobStatus.queued,
            audio_uri=f"{prefix}/audio.wav",
            status_uri=f"{prefix}/status.json",
            updated_at=utc_now(),
        )
        cancel_event = threading.Event()
        with self._lock:
            self._jobs[job_id] = record
            self._cancel_events[job_id] = cancel_event
        self._persist_record(record)
        parent_ctx = otel_context.get_current()
        task = asyncio.create_task(
            self._run_job(job_id, script, translate_to, parent_ctx),
            name=f"podcast-job-{job_id}",
        )
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(job_id, None))
        return record.model_copy()

    async def get_status(self, job_id: str) -> JobRecord:
        """Return the in-memory record, or ``status.json`` from GCS after restart."""
        self._prune()
        with self._lock:
            current = self._jobs.get(job_id)
        if current is not None:
            return self._maybe_fail_stale(current, store_in_memory=True)
        status_uri = f"{self.settings.job_prefix_uri(job_id)}/status.json"
        payload = await self._read_status_from_gcs(status_uri)
        if payload is None:
            raise JobNotFound(job_id)
        record = JobRecord.model_validate(payload)
        return self._maybe_fail_stale(record, store_in_memory=False)

    async def cancel(self, job_id: str) -> JobRecord:
        """Request cooperative cancel and persist ``cancelled`` immediately.

        GCS-only jobs (after restart) are marked cancelled without starting a worker.
        """
        updated: JobRecord | None = None
        with self._lock:
            record = self._jobs.get(job_id)
            if record is not None:
                if record.status in {JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled}:
                    return record.model_copy()
                event = self._cancel_events[job_id]
                event.set()
                updated = record.model_copy(
                    update={
                        "status": JobStatus.cancelled,
                        "error": None,
                        "progress": None,
                        "updated_at": utc_now(),
                    }
                )
                self._jobs[job_id] = updated
        if updated is not None:
            self._persist_record(updated)
            return updated.model_copy()

        status_uri = f"{self.settings.job_prefix_uri(job_id)}/status.json"
        payload = await self._read_status_from_gcs(status_uri)
        if payload is None:
            raise JobNotFound(job_id)
        loaded = JobRecord.model_validate(payload)
        if loaded.status in {JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled}:
            return loaded
        cancelled = loaded.model_copy(
            update={
                "status": JobStatus.cancelled,
                "error": None,
                "progress": None,
                "updated_at": utc_now(),
            }
        )
        self._persist_record(cancelled)
        return cancelled

    def _maybe_fail_stale(self, record: JobRecord, *, store_in_memory: bool) -> JobRecord:
        """Turn stale queued/running records into failed (no TTS resume)."""
        if record.status not in {JobStatus.queued, JobStatus.running}:
            return record.model_copy()
        if not self._is_stale(record):
            return record.model_copy()
        failed = record.model_copy(
            update={
                "status": JobStatus.failed,
                "error": _STALE_ERROR,
                "progress": None,
                "updated_at": utc_now(),
            }
        )
        if store_in_memory:
            with self._lock:
                current = self._jobs.get(record.job_id)
                if current is not None and current.status in {
                    JobStatus.queued,
                    JobStatus.running,
                }:
                    self._jobs[record.job_id] = failed
        self._persist_record(failed)
        return failed.model_copy()

    def _is_stale(self, record: JobRecord) -> bool:
        heartbeat = as_utc(record.updated_at)
        if heartbeat is None:
            return True
        age = utc_now() - heartbeat
        return age >= timedelta(seconds=self.settings.podcast_job_stale_ttl_sec)

    def _prune(self) -> None:
        """Drop terminal in-memory jobs older than the prune TTL."""
        cutoff = utc_now() - timedelta(seconds=self.settings.podcast_job_prune_ttl_sec)
        drop: list[str] = []
        with self._lock:
            for job_id, record in self._jobs.items():
                if record.status not in {
                    JobStatus.succeeded,
                    JobStatus.failed,
                    JobStatus.cancelled,
                }:
                    continue
                heartbeat = as_utc(record.updated_at)
                if heartbeat is not None and heartbeat <= cutoff:
                    drop.append(job_id)
            for job_id in drop:
                self._jobs.pop(job_id, None)
                self._cancel_events.pop(job_id, None)

    async def _run_job(
        self,
        job_id: str,
        script: PodcastScript,
        translate_to: str | None,
        parent_ctx: otel_context.Context,
    ) -> None:
        token = otel_context.attach(parent_ctx)
        try:
            with tracer.start_as_current_span("podcast.job") as span:
                span.set_attribute("podcast.job_id", job_id)
                if self._cancel_events[job_id].is_set():
                    self._mark(job_id, JobStatus.cancelled)
                    return
                await self._semaphore.acquire()
                try:
                    if self._cancel_events[job_id].is_set():
                        self._mark(job_id, JobStatus.cancelled)
                        return
                    self._mark(job_id, JobStatus.running, progress="starting")
                    await asyncio.get_running_loop().run_in_executor(
                        self._executor,
                        self._work_sync,
                        job_id,
                        script,
                        translate_to,
                        otel_context.get_current(),
                    )
                except SynthesisCancelled:
                    self._mark(job_id, JobStatus.cancelled)
                except Exception as exc:
                    logger.exception("Podcast job %s failed", job_id)
                    self._mark(job_id, JobStatus.failed, error=str(exc))
                finally:
                    self._semaphore.release()
                    self._persist_job(job_id)
        finally:
            otel_context.detach(token)

    def _work_sync(
        self,
        job_id: str,
        script: PodcastScript,
        translate_to: str | None,
        ctx: otel_context.Context,
    ) -> None:
        token = otel_context.attach(ctx)
        try:
            cancel_event = self._cancel_events[job_id]
            if cancel_event.is_set():
                raise SynthesisCancelled()

            handles: CloudHandles | None = None

            def clients() -> CloudHandles:
                nonlocal handles
                if handles is None:
                    handles = self._clients_factory()
                return handles

            if translate_to:
                current_lang = script.metadata.language_code.lower()
                target_lang = translate_to.lower()
                already = current_lang == target_lang or current_lang.startswith(f"{target_lang}-")
                if not already:
                    with tracer.start_as_current_span("podcast.translate"):
                        if self._translate is not None:
                            script = self._translate(script, translate_to, clients())
                        else:
                            script = _default_translate(
                                script,
                                translate_to,
                                clients(),
                                max_chars=self.settings.podcast_translate_max_chars,
                            )
                    checker = self._voice_catalog or _default_voice_catalog
                    checker(script, clients())

            if cancel_event.is_set():
                raise SynthesisCancelled()
            self._persist_job(job_id)

            def should_cancel() -> bool:
                return cancel_event.is_set()

            def on_progress(batch_idx: int, batch_count: int) -> None:
                self._mark(job_id, JobStatus.running, progress=f"batch {batch_idx}/{batch_count}")

            with tempfile.TemporaryDirectory(prefix=f"podcast-{job_id}-") as tmp:
                wav_path = Path(tmp) / "audio.wav"
                cloud = clients()
                self._synthesize(
                    cloud.tts_client,
                    script,
                    wav_path,
                    should_cancel=should_cancel,
                    on_progress=on_progress,
                )
                if cancel_event.is_set():
                    raise SynthesisCancelled()
                record = self._snapshot(job_id)
                with tracer.start_as_current_span("gcs.upload"):
                    self._upload_file(cloud.gcs_client, record.audio_uri, wav_path)
                if cancel_event.is_set():
                    try:
                        self._delete_file(cloud.gcs_client, record.audio_uri)
                    except Exception:
                        logger.exception("Failed to delete cancelled audio %s", record.audio_uri)
                    raise SynthesisCancelled()
            with self._lock:
                current = self._jobs[job_id]
                if current.status == JobStatus.cancelled or cancel_event.is_set():
                    raise SynthesisCancelled()
                self._jobs[job_id] = current.model_copy(
                    update={
                        "status": JobStatus.succeeded,
                        "error": None,
                        "progress": None,
                        "updated_at": utc_now(),
                    }
                )
        finally:
            otel_context.detach(token)

    def _snapshot(self, job_id: str) -> JobRecord:
        with self._lock:
            return self._jobs[job_id].model_copy()

    def _mark(
        self,
        job_id: str,
        status: JobStatus,
        *,
        error: str | None = None,
        progress: str | None = None,
    ) -> JobRecord:
        with self._lock:
            current = self._jobs[job_id]
            if current.status == JobStatus.cancelled and status not in {
                JobStatus.cancelled,
                JobStatus.failed,
            }:
                return current
            updates: dict[str, Any] = {"status": status, "updated_at": utc_now()}
            if error is not None:
                updates["error"] = error
            elif status is JobStatus.succeeded:
                updates["error"] = None
            if progress is not None:
                updates["progress"] = progress
            elif status in {JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled}:
                updates["progress"] = None
            updated = current.model_copy(update=updates)
            self._jobs[job_id] = updated
        self._persist_record(updated)
        return updated

    def _persist_job(self, job_id: str) -> None:
        try:
            self._persist_record(self._snapshot(job_id))
        except KeyError:
            logger.warning("Skip persist for unknown job %s", job_id)

    def _persist_record(self, record: JobRecord) -> None:
        try:
            stamped = record
            if stamped.updated_at is None:
                stamped = record.model_copy(update={"updated_at": utc_now()})
            handles = self._clients_factory()
            self._upload_bytes(
                handles.gcs_client,
                stamped.status_uri,
                stamped.model_dump_json().encode("utf-8"),
                "application/json",
            )
        except Exception:
            logger.exception("Failed to persist status.json for job %s", record.job_id)

    async def _read_status_from_gcs(self, status_uri: str) -> dict[str, Any] | None:
        def _load() -> dict[str, Any] | None:
            try:
                handles = self._clients_factory()
                raw = self._download_bytes(handles.gcs_client, status_uri)
            except Exception:
                logger.exception("Failed to read %s", status_uri)
                return None
            if raw is None:
                return None
            parsed: Any = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict):
                return None
            return parsed

        return await asyncio.get_running_loop().run_in_executor(self._executor, _load)


def default_clients_factory() -> CloudHandles:
    """Build EU TTS + GCS clients from Application Default Credentials."""
    from google.cloud import storage  # type: ignore[attr-defined]

    from tts_podcast_creator.logic.auth import get_credentials_and_project
    from tts_podcast_creator.logic.client import eu_tts_client

    credentials, project_id = get_credentials_and_project()
    return CloudHandles(
        credentials=credentials,
        project_id=project_id,
        tts_client=eu_tts_client(credentials),
        gcs_client=storage.Client(project=project_id, credentials=credentials),
    )


def _default_synthesize(
    tts_client: Any,
    script: PodcastScript,
    output_path: Path,
    **kwargs: Any,
) -> Path:
    from tts_podcast_creator.logic.client import synthesize_script

    return synthesize_script(tts_client, script, output_path, **kwargs)


def _default_translate(
    script: PodcastScript,
    translate_to: str,
    handles: CloudHandles,
    *,
    max_chars: int,
) -> PodcastScript:
    from tts_podcast_creator.logic.translator import PodcastTranslator

    translator = PodcastTranslator(project_id=handles.project_id, credentials=handles.credentials)
    return translator.translate_script(script, translate_to, max_chars=max_chars)


def _default_voice_catalog(script: PodcastScript, handles: CloudHandles) -> None:
    from tts_podcast_creator.logic.client import assert_voices_in_catalog

    assert_voices_in_catalog(handles.tts_client, script)


def _default_upload_bytes(gcs_client: Any, gcs_uri: str, data: bytes, content_type: str) -> None:
    from tts_podcast_creator.logic.storage import upload_bytes

    upload_bytes(gcs_client, gcs_uri, data, content_type)


def _default_download_bytes(gcs_client: Any, gcs_uri: str) -> bytes | None:
    from tts_podcast_creator.logic.storage import download_bytes

    return download_bytes(gcs_client, gcs_uri)


def _default_upload_file(gcs_client: Any, gcs_uri: str, source_path: Path) -> None:
    from tts_podcast_creator.logic.storage import upload_file

    upload_file(gcs_client, gcs_uri, source_path)


def _default_delete_file(gcs_client: Any, gcs_uri: str) -> None:
    from tts_podcast_creator.logic.storage import delete_file

    delete_file(gcs_client, gcs_uri)


def validate_payload(payload: str, settings: Settings) -> dict[str, Any]:
    """Return a validation summary for MCP ``validate_script``."""
    try:
        script = PodcastScript.from_payload(payload, max_bytes=settings.podcast_max_script_bytes)
    except (ScriptPayloadError, Exception) as exc:
        return {
            "valid": False,
            "title": "",
            "language_code": "",
            "speakers": [],
            "turn_count": 0,
            "total_characters": 0,
            "error": str(exc),
        }
    return {
        "valid": True,
        "title": script.metadata.title,
        "language_code": script.metadata.language_code,
        "speakers": list(script.voices),
        "turn_count": script.turn_count,
        "total_characters": script.total_character_count,
        "error": None,
    }
