"""Shared helpers for ADK adapter unit tests."""

from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn

from tests.unit.mcp.helpers import FakeGcs, mcp_app, write_fake_wav
from tts_audio_conversation.logic.service import AudioConversationService
from tts_audio_conversation.mcp.server import MCP_PATH


def free_port() -> int:
    """Bind an ephemeral localhost port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def gcs_download_fn(store: FakeGcs) -> Callable[[str], bytes | None]:
    """Adapt FakeGcs.download_bytes(client, uri) to ``(uri) -> bytes``."""

    def _download(uri: str) -> bytes | None:
        return store.download_bytes(None, uri)

    return _download


def delayed_synth(delay_sec: float) -> Callable[..., Path]:
    """Return a synthesizer that sleeps, then writes a tiny WAV."""

    def _synth(_client: Any, _script: Any, output_path: Path, **_kwargs: Any) -> Path:
        time.sleep(delay_sec)
        return write_fake_wav(output_path)

    return _synth


@dataclass
class MockedMcpHttp:
    """In-process FastMCP HTTP server with mocked TTS/GCS."""

    url: str
    service: AudioConversationService
    gcs: FakeGcs


@asynccontextmanager
async def mocked_mcp_http(
    synthesize_fn: Callable[..., Path],
    **kwargs: Any,
) -> AsyncIterator[MockedMcpHttp]:
    """Serve ``mcp_app`` over Streamable HTTP on a free loopback port."""
    mcp, service, store = mcp_app(synthesize_fn, **kwargs)
    port = free_port()
    asgi = mcp.http_app(path=MCP_PATH, transport="http", host_origin_protection=False)
    config = uvicorn.Config(
        asgi,
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
        yield MockedMcpHttp(
            url=f"http://127.0.0.1:{port}{MCP_PATH}",
            service=service,
            gcs=store,
        )
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(serve_task, timeout=15)
        except TimeoutError:
            serve_task.cancel()
        finally:
            await service.close()
