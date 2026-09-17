"""Shared helpers for in-process FastMCP unit tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from tts_audio_conversation.adk.mcp_client import gcs_stub_blob_path
from tts_audio_conversation.logic.jobs.job import CloudHandles
from tts_audio_conversation.logic.jobs.manager import JobManager
from tts_audio_conversation.logic.service import AudioConversationService
from tts_audio_conversation.logic.settings import Settings
from tts_audio_conversation.logic.storage import StorageService
from tts_audio_conversation.logic.translator import ConversationTranslator
from tts_audio_conversation.logic.voices import VoiceCatalogService
from tts_audio_conversation.mcp.server import create_server


def mcp_settings(**kwargs: Any) -> Settings:
    """Build Settings that do not require ADC or a real bucket."""
    defaults: dict[str, Any] = {
        "google_cloud_project": "test-proj",
        "audio_conversation_gcs_staging_bucket": "test-eu-bucket",
        "audio_conversation_gcs_prefix": "conversation",
        "audio_conversation_max_concurrent_jobs": 4,
        "audio_conversation_max_script_bytes": 512000,
        "otel_traces_exporter": "none",
    }
    defaults.update(kwargs)
    return Settings(**defaults)


def mock_handles() -> CloudHandles:
    """Return dummy SDK handles for injected JobManager tests."""
    return CloudHandles(
        tts_client=MagicMock(),
        gcs_client=MagicMock(),
    )


class FakeGcs:
    """In-memory gs:// object store used as the MCP output sink."""

    def __init__(self, persist_dir: Path | None = None) -> None:
        """Optionally mirror blobs to ``persist_dir`` for an ADK API-server child."""
        self.objects: dict[str, bytes] = {}
        self.persist_dir = persist_dir
        if persist_dir is not None:
            persist_dir.mkdir(parents=True, exist_ok=True)

    def upload_bytes(self, _client: Any, uri: str, data: bytes, _content_type: str) -> None:
        """Store bytes at a URI."""
        self.objects[uri] = data
        self._mirror(uri, data)

    def upload_file(self, _client: Any, uri: str, source_path: Path) -> None:
        """Store a local file at a URI."""
        data = Path(source_path).read_bytes()
        self.objects[uri] = data
        self._mirror(uri, data)

    def download_bytes(self, _client: Any, uri: str) -> bytes | None:
        """Return stored bytes or None."""
        return self.objects.get(uri)

    def delete_file(self, _client: Any, uri: str) -> None:
        """Remove a stored URI if present."""
        self.objects.pop(uri, None)
        if self.persist_dir is not None:
            gcs_stub_blob_path(self.persist_dir, uri).unlink(missing_ok=True)

    def _mirror(self, uri: str, data: bytes) -> None:
        if self.persist_dir is None:
            return
        path = gcs_stub_blob_path(self.persist_dir, uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


class FakeStorage(StorageService):
    """StorageService backed by FakeGcs (ignores real client)."""

    def __init__(self, store: FakeGcs) -> None:
        """Bind an in-memory store."""
        self._store = store
        self._gcs_client = MagicMock()

    def upload_file(
        self,
        gcs_uri: str,
        source_path: Path | str,
        content_type: str = "audio/wav",
    ) -> None:
        """Upload a local file into FakeGcs."""
        _ = content_type
        self._store.upload_file(None, gcs_uri, Path(source_path))

    def upload_bytes(
        self,
        gcs_uri: str,
        data: bytes,
        content_type: str = "application/json",
    ) -> None:
        """Upload bytes into FakeGcs."""
        self._store.upload_bytes(None, gcs_uri, data, content_type)

    def download_file(self, gcs_uri: str, destination_path: Path | str) -> None:
        """Download from FakeGcs to a local path."""
        raw = self._store.download_bytes(None, gcs_uri)
        if raw is None:
            raise FileNotFoundError(gcs_uri)
        target = Path(destination_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)

    def download_bytes(self, gcs_uri: str) -> bytes | None:
        """Download bytes from FakeGcs."""
        return self._store.download_bytes(None, gcs_uri)

    def delete_file(self, gcs_uri: str) -> None:
        """Delete from FakeGcs."""
        self._store.delete_file(None, gcs_uri)


def silent_voice_catalog(_script: Any, _handles: Any) -> None:
    """Skip EU list_voices in unit tests (schema Chirp3-HD is enough)."""
    return None


def job_manager(
    synthesize_fn: Any,
    *,
    settings: Settings | None = None,
    gcs: FakeGcs | None = None,
    voice_catalog_fn: Any = silent_voice_catalog,
) -> tuple[JobManager, FakeGcs]:
    """Create a JobManager with mocked TTS/GCS."""
    store = gcs or FakeGcs()
    manager = JobManager(
        settings or mcp_settings(),
        clients_factory=mock_handles,
        synthesize_fn=synthesize_fn,
        upload_bytes_fn=store.upload_bytes,
        download_bytes_fn=store.download_bytes,
        upload_file_fn=store.upload_file,
        delete_file_fn=store.delete_file,
        voice_catalog_fn=voice_catalog_fn,
    )
    return manager, store


def make_service(
    synthesize_fn: Any,
    *,
    settings: Settings | None = None,
    gcs: FakeGcs | None = None,
    voice_catalog_fn: Any = silent_voice_catalog,
    translate_fn: Any = None,
) -> tuple[AudioConversationService, JobManager, FakeGcs]:
    """Build an injectable facade + manager + fake GCS for unit tests."""
    store = gcs or FakeGcs()
    cfg = settings or mcp_settings()
    manager, _ = job_manager(
        synthesize_fn,
        settings=cfg,
        gcs=store,
        voice_catalog_fn=voice_catalog_fn,
    )
    storage = FakeStorage(store)
    voices = VoiceCatalogService(MagicMock())
    voices.assert_in_catalog = MagicMock()  # type: ignore[method-assign]
    translator = MagicMock(spec=ConversationTranslator)
    if translate_fn is not None:
        translator.translate_script = translate_fn
    service = AudioConversationService(
        settings=cfg,
        jobs=manager,
        voices=voices,
        translator=translator,
        storage=storage,
    )
    return service, manager, store


def mcp_app(synthesize_fn: Any, **kwargs: Any) -> tuple[Any, AudioConversationService, FakeGcs]:
    """Return ``(FastMCP, AudioConversationService, FakeGcs)`` for Client tests."""
    service, _manager, store = make_service(synthesize_fn, **kwargs)
    return create_server(service), service, store


def write_fake_wav(output_path: Path) -> Path:
    """Write a tiny payload the job worker can upload as audio.wav."""
    dest = Path(output_path)
    dest.write_bytes(b"RIFFFAKE")
    return dest


def instant_synth(_client: Any, _script: Any, output_path: Path, **_kwargs: Any) -> Path:
    """No-op synthesizer used by unit tests that only care about job plumbing."""
    return write_fake_wav(output_path)


async def upload_and_start(client: Any, script_payload: str) -> dict[str, Any]:
    """Upload inline script then start_conversation; return start tool data."""
    uploaded = tool_data(await client.call_tool("upload_script", {"script": script_payload}))
    started = tool_data(
        await client.call_tool("start_conversation", {"script_uri": uploaded["script_uri"]})
    )
    started["script_uri"] = uploaded["script_uri"]
    return started


def tool_data(result: Any) -> dict[str, Any]:
    """Unwrap FastMCP CallToolResult into a plain dict."""
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data
    if data is not None and hasattr(data, "model_dump"):
        dumped = data.model_dump()
        if isinstance(dumped, dict):
            return dumped
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        inner = structured.get("result", structured)
        if isinstance(inner, dict):
            return inner
    content = getattr(result, "content", None)
    if content:
        text = getattr(content[0], "text", None)
        if isinstance(text, str):
            parsed: Any = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
    raise AssertionError(f"Cannot extract tool data from {result!r}")
