"""In-process conversation jobs: validate immediately, synthesize in the background."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace

from tts_audio_conversation.logic.exceptions import JobNotFound, SynthesisCancelled
from tts_audio_conversation.logic.jobs.job import (
    ClientsFactory,
    CloudHandles,
    ConversationCreationJob,
    DeleteFileFn,
    SynthesizeFn,
    UploadFileFn,
    VoiceCatalogFn,
)
from tts_audio_conversation.logic.jobs.models import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    JobRecord,
    JobStatus,
)
from tts_audio_conversation.logic.models import ConversationScript
from tts_audio_conversation.logic.settings import Settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

UploadBytesFn = Callable[[Any, str, bytes, str], None]
DownloadBytesFn = Callable[[Any, str], bytes | None]


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
    """Concurrent conversation jobs with cooperative cancel between TTS batches.

    ``start`` never waits on TTS. It may write ``status.json`` and call EU
    ``list_voices``. Extra jobs beyond the concurrency cap stay ``queued``.
    Status is persisted only on real status transitions (table-driven FSM).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        clients_factory: ClientsFactory | None = None,
        synthesize_fn: SynthesizeFn | None = None,
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
        self._upload_bytes = upload_bytes_fn or _default_upload_bytes
        self._download_bytes = download_bytes_fn or _default_download_bytes
        self._upload_file = upload_file_fn or _default_upload_file
        self._delete_file = delete_file_fn or _default_delete_file
        self._voice_catalog = voice_catalog_fn
        self._jobs: dict[str, JobRecord] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = threading.Lock()
        self._transition_lock = threading.Lock()
        self._handles_lock = threading.Lock()
        self._cached_handles: CloudHandles | None = None
        self._semaphore = asyncio.Semaphore(settings.audio_conversation_max_concurrent_jobs)
        pool_size = max(settings.audio_conversation_max_concurrent_jobs, 4)
        self._executor = ThreadPoolExecutor(
            max_workers=pool_size,
            thread_name_prefix="conversation-tts",
        )
        self._control_executor = ThreadPoolExecutor(
            max_workers=pool_size,
            thread_name_prefix="conversation-ctl",
        )

    def _handles(self) -> CloudHandles:
        """Return memoized Cloud SDK clients, creating them on first use."""
        with self._handles_lock:
            if self._cached_handles is None:
                self._cached_handles = self._clients_factory()
            return self._cached_handles

    def _preflight_voices(self, script: ConversationScript) -> None:
        """Confirm Chirp 3 HD names exist in the EU catalog before enqueueing."""
        handles = self._handles()
        checker = self._voice_catalog or _default_voice_catalog
        checker(script, handles)

    async def start(self, script_uri: str) -> JobRecord:
        """Load script from GCS, preflight voices, persist queued status, schedule work.

        Does not call ``synthesize_speech``. Writes ``status.json`` before return.
        """
        self._prune()
        self.settings.require_gcs_bucket()
        loop = asyncio.get_running_loop()
        script = await loop.run_in_executor(self._control_executor, self._load_script, script_uri)
        await loop.run_in_executor(self._executor, self._preflight_voices, script)
        job_id = str(uuid.uuid4())
        prefix = self.settings.job_prefix_uri(job_id)
        record = JobRecord(
            job_id=job_id,
            status=JobStatus.queued,
            script_uri=script_uri,
            audio_uri=f"{prefix}/output/audio.wav",
            status_uri=f"{prefix}/status.json",
            updated_at=utc_now(),
        )
        cancel_event = threading.Event()
        with self._lock:
            self._jobs[job_id] = record
            self._cancel_events[job_id] = cancel_event
        try:
            await loop.run_in_executor(self._control_executor, self._persist_record, record)
        except BaseException:
            with self._lock:
                self._jobs.pop(job_id, None)
                self._cancel_events.pop(job_id, None)
            raise
        parent_ctx = otel_context.get_current()
        task = asyncio.create_task(
            self._run_job(job_id, script, parent_ctx),
            name=f"conversation-job-{job_id}",
        )
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(job_id, None))
        return record.model_copy()

    def _load_script(self, script_uri: str) -> ConversationScript:
        """Download and parse a script from GCS."""
        from tts_audio_conversation.logic.exceptions import ScriptPayloadError

        handles = self._handles()
        raw = self._download_bytes(handles.gcs_client, script_uri)
        if raw is None:
            raise ScriptPayloadError(f"Object not found: {script_uri}")
        return ConversationScript.from_payload(
            raw.decode("utf-8"),
            max_bytes=self.settings.audio_conversation_max_script_bytes,
        )

    async def get_status(self, job_id: str) -> JobRecord:
        """Return the in-memory record, or ``status.json`` from GCS after restart."""
        self._prune()
        with self._lock:
            current = self._jobs.get(job_id)
        if current is not None:
            return current.model_copy()
        status_uri = f"{self.settings.job_prefix_uri(job_id)}/status.json"
        payload = await self._read_status_from_gcs(status_uri)
        if payload is None:
            raise JobNotFound(job_id)
        return JobRecord.model_validate(payload)

    async def cancel(self, job_id: str) -> JobRecord:
        """Request cooperative cancel and persist ``cancelled`` immediately.

        GCS-only jobs (after restart) are marked cancelled without starting a worker.
        """
        with self._lock:
            record = self._jobs.get(job_id)
            if record is not None:
                if record.status in TERMINAL_STATUSES:
                    return record.model_copy()
                self._cancel_events[job_id].set()
        if record is not None:
            return await self._atransition(job_id, JobStatus.cancelled)

        status_uri = f"{self.settings.job_prefix_uri(job_id)}/status.json"
        payload = await self._read_status_from_gcs(status_uri)
        if payload is None:
            raise JobNotFound(job_id)
        loaded = JobRecord.model_validate(payload)
        if loaded.status in TERMINAL_STATUSES:
            return loaded
        cancelled = loaded.model_copy(
            update={
                "status": JobStatus.cancelled,
                "error": None,
                "updated_at": utc_now(),
            }
        )
        await asyncio.get_running_loop().run_in_executor(
            self._control_executor, self._persist_record, cancelled
        )
        return cancelled

    async def close(self) -> None:
        """Request cancel on active jobs, await tasks, then shut down executors."""
        with self._lock:
            for event in self._cancel_events.values():
                event.set()
            tasks = list(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._executor.shutdown(wait=True)
        self._control_executor.shutdown(wait=True)

    def _prune(self) -> None:
        """Drop terminal in-memory jobs older than the prune TTL."""
        cutoff = utc_now() - timedelta(seconds=self.settings.audio_conversation_job_prune_ttl_sec)
        drop: list[str] = []
        with self._lock:
            for job_id, record in self._jobs.items():
                if record.status not in TERMINAL_STATUSES:
                    continue
                updated = as_utc(record.updated_at)
                if updated is not None and updated <= cutoff:
                    drop.append(job_id)
            for job_id in drop:
                self._jobs.pop(job_id, None)
                self._cancel_events.pop(job_id, None)

    async def _run_job(
        self,
        job_id: str,
        script: ConversationScript,
        parent_ctx: otel_context.Context,
    ) -> None:
        token = otel_context.attach(parent_ctx)
        try:
            with tracer.start_as_current_span("conversation.job") as span:
                span.set_attribute("conversation.job_id", job_id)
                if self._cancel_events[job_id].is_set():
                    await self._atransition(job_id, JobStatus.cancelled)
                    return
                await self._semaphore.acquire()
                try:
                    if self._cancel_events[job_id].is_set():
                        await self._atransition(job_id, JobStatus.cancelled)
                        return
                    marked = await self._atransition(job_id, JobStatus.running)
                    if marked.status in TERMINAL_STATUSES:
                        return
                    await asyncio.get_running_loop().run_in_executor(
                        self._executor,
                        self._work_sync,
                        job_id,
                        script,
                    )
                except SynthesisCancelled:
                    await self._atransition(job_id, JobStatus.cancelled)
                except Exception as exc:
                    logger.exception("Conversation job %s failed", job_id)
                    await self._atransition(job_id, JobStatus.failed, error=str(exc))
                finally:
                    self._semaphore.release()
        finally:
            otel_context.detach(token)

    def _work_sync(
        self,
        job_id: str,
        script: ConversationScript,
    ) -> None:
        cancel_event = self._cancel_events[job_id]
        record = self._snapshot(job_id)
        handles = self._handles()
        job = ConversationCreationJob(
            job_id,
            script,
            audio_uri=record.audio_uri,
            should_cancel=cancel_event.is_set,
            tts_client=handles.tts_client,
            gcs_client=handles.gcs_client,
            synthesize_fn=self._synthesize,
            upload_file_fn=self._upload_file,
            delete_file_fn=self._delete_file,
        )
        job.execute()
        with self._lock:
            current = self._jobs[job_id]
            if current.status in {JobStatus.failed, JobStatus.cancelled}:
                if current.status == JobStatus.cancelled or cancel_event.is_set():
                    raise SynthesisCancelled()
                return
            if cancel_event.is_set():
                try:
                    self._delete_file(handles.gcs_client, record.audio_uri)
                except Exception:
                    logger.exception("Failed to delete late-cancelled audio %s", record.audio_uri)
                raise SynthesisCancelled()
        self._transition(job_id, JobStatus.succeeded)

    def _snapshot(self, job_id: str) -> JobRecord:
        with self._lock:
            return self._jobs[job_id].model_copy()

    async def _atransition(
        self,
        job_id: str,
        new_status: JobStatus,
        *,
        error: str | None = None,
    ) -> JobRecord:
        """Apply a status transition with GCS persist on the control executor."""

        def _do() -> JobRecord:
            return self._transition(job_id, new_status, error=error)

        return await asyncio.get_running_loop().run_in_executor(self._control_executor, _do)

    def _transition(
        self,
        job_id: str,
        new_status: JobStatus,
        *,
        error: str | None = None,
    ) -> JobRecord:
        """Apply an allowed status transition and persist ``status.json``.

        Persistence completes before the in-memory map is updated. Concurrent
        transitions are serialized; a failed persist leaves the previous record.
        """
        with self._transition_lock:
            with self._lock:
                current = self._jobs[job_id]
                if new_status not in ALLOWED_TRANSITIONS[current.status]:
                    return current.model_copy()
                updates: dict[str, Any] = {
                    "status": new_status,
                    "updated_at": utc_now(),
                }
                if error is not None:
                    updates["error"] = error
                elif new_status is JobStatus.succeeded:
                    updates["error"] = None
                updated = current.model_copy(update=updates)
            self._persist_record(updated)
            with self._lock:
                self._jobs[job_id] = updated
            return updated

    def _persist_record(self, record: JobRecord) -> None:
        try:
            stamped = record
            if stamped.updated_at is None:
                stamped = record.model_copy(update={"updated_at": utc_now()})
            handles = self._handles()
            self._upload_bytes(
                handles.gcs_client,
                stamped.status_uri,
                stamped.model_dump_json().encode("utf-8"),
                "application/json",
            )
        except Exception:
            logger.exception("Failed to persist status.json for job %s", record.job_id)
            raise

    async def _read_status_from_gcs(self, status_uri: str) -> dict[str, Any] | None:
        def _load() -> dict[str, Any] | None:
            handles = self._handles()
            raw = self._download_bytes(handles.gcs_client, status_uri)
            if raw is None:
                return None
            parsed: Any = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict):
                return None
            return parsed

        return await asyncio.get_running_loop().run_in_executor(self._control_executor, _load)


def default_clients_factory() -> CloudHandles:
    """Build EU TTS + GCS clients from Application Default Credentials."""
    from google.cloud import storage  # type: ignore[attr-defined]

    from tts_audio_conversation.logic.auth import get_credentials_and_project
    from tts_audio_conversation.logic.client import eu_tts_client

    credentials, project_id = get_credentials_and_project()
    return CloudHandles(
        tts_client=eu_tts_client(credentials),
        gcs_client=storage.Client(project=project_id, credentials=credentials),
    )


def _default_synthesize(
    tts_client: Any,
    script: ConversationScript,
    output_path: Any,
    **kwargs: Any,
) -> Any:
    from pathlib import Path

    from tts_audio_conversation.logic.client import synthesize_script

    return synthesize_script(tts_client, script, Path(output_path), **kwargs)


def _default_voice_catalog(script: ConversationScript, handles: CloudHandles) -> None:
    from tts_audio_conversation.logic.client import assert_voices_in_catalog

    assert_voices_in_catalog(handles.tts_client, script)


def _default_upload_bytes(gcs_client: Any, gcs_uri: str, data: bytes, content_type: str) -> None:
    from tts_audio_conversation.logic.storage import upload_bytes

    upload_bytes(gcs_client, gcs_uri, data, content_type)


def _default_download_bytes(gcs_client: Any, gcs_uri: str) -> bytes | None:
    from tts_audio_conversation.logic.storage import download_bytes

    return download_bytes(gcs_client, gcs_uri)


def _default_upload_file(gcs_client: Any, gcs_uri: str, source_path: Any) -> None:
    from pathlib import Path

    from tts_audio_conversation.logic.storage import upload_file

    upload_file(gcs_client, gcs_uri, Path(source_path))


def _default_delete_file(gcs_client: Any, gcs_uri: str) -> None:
    from tts_audio_conversation.logic.storage import delete_file

    delete_file(gcs_client, gcs_uri)
