"""Shared helpers for live FastMCP HTTP integration tests."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import time
import wave
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from dotenv import load_dotenv
from google.cloud import storage  # type: ignore[attr-defined]

from tts_podcast_creator.logic.auth import get_credentials_and_project
from tts_podcast_creator.logic.settings import Settings
from tts_podcast_creator.logic.storage import delete_file
from tts_podcast_creator.mcp.jobs import JobManager, JobStatus
from tts_podcast_creator.mcp.server import MCP_PATH, create_server

load_dotenv()

START_DEADLINE_SEC = 2.0
SHORT_POLL_TIMEOUT_SEC = 120.0
LONG_POLL_TIMEOUT_SEC = 1500.0


def delete_gcs_blob(gcs_client: storage.Client, gcs_uri: str) -> None:
    """Delete the test blob unless KEEP_TEST_ARTIFACTS=true."""
    if os.environ.get("KEEP_TEST_ARTIFACTS", "").strip().lower() == "true":
        return
    try:
        delete_file(gcs_client, gcs_uri)
    except Exception:
        pass


def require_mcp_live_env() -> tuple[str, str]:
    """Need ADC project plus an EU bucket (explicit or derived from the test URI)."""
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    bucket = os.environ.get("PODCAST_GCS_BUCKET")
    test_uri = os.environ.get("PODCAST_TEST_GCS_URI")
    if not project_id:
        pytest.skip("GOOGLE_CLOUD_PROJECT must be set for integration tests")
    if not bucket:
        if not test_uri or not test_uri.startswith("gs://"):
            pytest.skip(
                "PODCAST_GCS_BUCKET or PODCAST_TEST_GCS_URI must be set for MCP integration tests"
            )
        bucket = test_uri[5:].split("/", 1)[0]
    return project_id, bucket


def free_port() -> int:
    """Bind an ephemeral localhost port for the MCP HTTP server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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


@dataclass
class LiveMcp:
    """Running MCP HTTP server plus the in-process job manager."""

    manager: JobManager
    url: str
    gcs_client: storage.Client
    bucket: str
    prefix: str


@asynccontextmanager
async def live_mcp_http(
    prefix: str,
    *,
    max_concurrent_jobs: int = 2,
) -> AsyncIterator[LiveMcp]:
    """Start FastMCP Streamable HTTP with a real JobManager (ADC + EU TTS + GCS)."""
    project_id, bucket = require_mcp_live_env()
    credentials, resolved_proj = get_credentials_and_project(project_id)
    gcs_client = storage.Client(project=resolved_proj, credentials=credentials)
    settings = Settings(
        google_cloud_project=resolved_proj,
        podcast_gcs_bucket=bucket,
        podcast_gcs_prefix=prefix,
        podcast_max_concurrent_jobs=max_concurrent_jobs,
        otel_traces_exporter="none",
    )
    manager = JobManager(settings)
    mcp = create_server(manager)
    port = free_port()
    app = mcp.http_app(path=MCP_PATH, transport="http", host_origin_protection=False)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="on",
        timeout_graceful_shutdown=2,
    )
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve())
    try:
        deadline = time.monotonic() + 10.0
        while not server.started:
            if serve_task.done():
                raise AssertionError(f"MCP HTTP server failed to start: {serve_task.exception()!r}")
            if time.monotonic() > deadline:
                raise AssertionError("MCP HTTP server did not start within 10s")
            await asyncio.sleep(0.05)
        yield LiveMcp(
            manager=manager,
            url=f"http://127.0.0.1:{port}{MCP_PATH}",
            gcs_client=gcs_client,
            bucket=bucket,
            prefix=prefix,
        )
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(serve_task, timeout=15)
        except (TimeoutError, asyncio.CancelledError):
            serve_task.cancel()
        manager._executor.shutdown(wait=False)
        manager._control_executor.shutdown(wait=False)


def assert_mono_wav(path: Path, *, min_duration_secs: float | None = None) -> float:
    """Require a non-empty mono WAV. Return duration in seconds."""
    assert path.stat().st_size > 0
    with wave.open(str(path), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getnframes() > 0
        duration = wav_file.getnframes() / float(wav_file.getframerate())
    if min_duration_secs is not None:
        assert duration > min_duration_secs, (
            f"Expected audio duration > {min_duration_secs}s, got {duration:.1f}s"
        )
    return duration


def wav_sample_rate(path: Path) -> int:
    """Return the WAV sample rate."""
    with wave.open(str(path), "rb") as wav_file:
        return int(wav_file.getframerate())


async def poll_until_terminal(
    client: Any,
    job_id: str,
    *,
    timeout: float,
    interval: float = 0.25,
) -> dict[str, Any]:
    """Poll get_podcast_status until succeeded/failed/cancelled or timeout."""
    status: dict[str, Any] | None = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = tool_data(await client.call_tool("get_podcast_status", {"job_id": job_id}))
        if status["status"] in {JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled}:
            return status
        await asyncio.sleep(interval)
    raise AssertionError(f"job {job_id} did not finish; last={status}")
