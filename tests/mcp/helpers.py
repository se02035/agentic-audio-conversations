"""Shared helpers for in-process FastMCP unit tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from tts_audio_conversation.logic.settings import Settings
from tts_audio_conversation.mcp.jobs import CloudHandles, JobManager
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
        credentials=MagicMock(),
        project_id="test-proj",
        tts_client=MagicMock(),
        gcs_client=MagicMock(),
    )


class FakeGcs:
    """In-memory gs:// object store used as the MCP output sink."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def upload_bytes(self, _client: Any, uri: str, data: bytes, _content_type: str) -> None:
        """Store bytes at a URI."""
        self.objects[uri] = data

    def upload_file(self, _client: Any, uri: str, source_path: Path) -> None:
        """Store a local file at a URI."""
        self.objects[uri] = Path(source_path).read_bytes()

    def download_bytes(self, _client: Any, uri: str) -> bytes | None:
        """Return stored bytes or None."""
        return self.objects.get(uri)

    def delete_file(self, _client: Any, uri: str) -> None:
        """Remove a stored URI if present."""
        self.objects.pop(uri, None)


def silent_voice_catalog(_script: Any, _handles: Any) -> None:
    """Skip EU list_voices in unit tests (schema Chirp3-HD is enough)."""
    return None


def job_manager(
    synthesize_fn: Any,
    *,
    settings: Settings | None = None,
    gcs: FakeGcs | None = None,
    translate_fn: Any = None,
    voice_catalog_fn: Any = silent_voice_catalog,
) -> tuple[JobManager, FakeGcs]:
    """Create a JobManager with mocked TTS/GCS and a FastMCP-ready fake store."""
    store = gcs or FakeGcs()
    manager = JobManager(
        settings or mcp_settings(),
        clients_factory=mock_handles,
        synthesize_fn=synthesize_fn,
        translate_fn=translate_fn,
        upload_bytes_fn=store.upload_bytes,
        download_bytes_fn=store.download_bytes,
        upload_file_fn=store.upload_file,
        delete_file_fn=store.delete_file,
        voice_catalog_fn=voice_catalog_fn,
    )
    return manager, store


def mcp_app(synthesize_fn: Any, **kwargs: Any) -> tuple[Any, JobManager, FakeGcs]:
    """Return ``(FastMCP, JobManager, FakeGcs)`` for in-process Client tests."""
    manager, store = job_manager(synthesize_fn, **kwargs)
    return create_server(manager), manager, store


def write_fake_wav(output_path: Path) -> Path:
    """Write a tiny payload the job worker can upload as audio.wav."""
    dest = Path(output_path)
    dest.write_bytes(b"RIFFFAKE")
    return dest


def instant_synth(_client: Any, _script: Any, output_path: Path, **_kwargs: Any) -> Path:
    """No-op synthesizer used by unit tests that only care about job plumbing."""
    return write_fake_wav(output_path)


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
