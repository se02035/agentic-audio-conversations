"""Launch the real ``adk api_server`` CLI and call its REST API (not ``adk web``)."""

from __future__ import annotations

import asyncio
import base64
import os
import sys
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from tests.unit.adk.api_events import function_responses
from tests.unit.adk.helpers import free_port
from tts_audio_conversation.adk.config import repo_root
from tts_audio_conversation.adk.mcp_client import GCS_STUB_DIR_ENV

ADK_APP_NAME = "adk"
ADK_USER_ID = "adk-it"
_START_TIMEOUT_SEC = 45.0
_RUN_TIMEOUT_SEC = 180.0
_LOG_TAIL_CHARS = 8000


def _agents_dir() -> Path:
    """Return ``src/tts_audio_conversation/adk`` (single-agent folder for ADK)."""
    return repo_root() / "src" / "tts_audio_conversation" / "adk"


@dataclass
class AdkApiServer:
    """Running ADK API server plus an HTTP client."""

    base_url: str
    log_chunks: list[str]
    _client: httpx.AsyncClient = field(repr=False)
    _proc: asyncio.subprocess.Process = field(repr=False)
    _drain: asyncio.Task[None] = field(repr=False)
    app_name: str = ADK_APP_NAME
    user_id: str = ADK_USER_ID

    @property
    def logs(self) -> str:
        """Return captured api_server stdout/stderr."""
        return "".join(self.log_chunks)

    async def list_apps(self) -> list[str]:
        """``GET /list-apps``."""
        response = await self._client.get(f"{self.base_url}/list-apps")
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return [str(item) for item in payload]
        raise AssertionError(f"Unexpected list-apps payload: {payload!r}")

    async def create_session(self, session_id: str) -> dict[str, Any]:
        """``POST /apps/{app}/users/{user}/sessions/{session}``."""
        url = f"{self.base_url}/apps/{self.app_name}/users/{self.user_id}/sessions/{session_id}"
        response = await self._client.post(url, json={})
        response.raise_for_status()
        payload = response.json()
        assert isinstance(payload, dict)
        return payload

    async def save_artifact(
        self,
        session_id: str,
        filename: str,
        data: bytes,
        mime_type: str,
    ) -> dict[str, Any]:
        """``POST .../sessions/{session}/artifacts``."""
        url = (
            f"{self.base_url}/apps/{self.app_name}/users/{self.user_id}"
            f"/sessions/{session_id}/artifacts"
        )
        response = await self._client.post(
            url,
            json={
                "filename": filename,
                "artifact": {
                    "inlineData": {
                        "displayName": filename,
                        "mimeType": mime_type,
                        "data": base64.b64encode(data).decode("ascii"),
                    }
                },
            },
        )
        response.raise_for_status()
        payload = response.json()
        assert isinstance(payload, dict)
        return payload

    async def run(
        self,
        session_id: str,
        text: str,
        *,
        inline_files: Sequence[Mapping[str, Any]] | None = None,
    ) -> list[Any]:
        """``POST /run`` with a user text part and optional inline file parts."""
        parts: list[dict[str, Any]] = [{"text": text}]
        if inline_files:
            parts.extend(dict(part) for part in inline_files)
        response = await self._client.post(
            f"{self.base_url}/run",
            json={
                "appName": self.app_name,
                "userId": self.user_id,
                "sessionId": session_id,
                "newMessage": {"role": "user", "parts": parts},
            },
            timeout=_RUN_TIMEOUT_SEC,
        )
        if response.is_error:
            raise AssertionError(
                f"POST /run failed: HTTP {response.status_code}: {response.text[:2000]}\n"
                f"{self.logs[-_LOG_TAIL_CHARS:]}"
            )
        payload = response.json()
        assert isinstance(payload, list)
        return payload

    async def get_session(self, session_id: str) -> dict[str, Any]:
        """``GET /apps/{app}/users/{user}/sessions/{session}``."""
        url = f"{self.base_url}/apps/{self.app_name}/users/{self.user_id}/sessions/{session_id}"
        response = await self._client.get(url)
        response.raise_for_status()
        payload = response.json()
        assert isinstance(payload, dict)
        return payload

    async def list_artifacts(self, session_id: str) -> list[str]:
        """``GET .../sessions/{session}/artifacts``."""
        url = (
            f"{self.base_url}/apps/{self.app_name}/users/{self.user_id}"
            f"/sessions/{session_id}/artifacts"
        )
        response = await self._client.get(url)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return [str(item) for item in payload]
        raise AssertionError(f"Unexpected artifacts payload: {payload!r}")

    async def wait_for_function_responses(
        self,
        session_id: str,
        name: str,
        *,
        min_count: int,
        timeout_sec: float,
    ) -> list[dict[str, Any]]:
        """Poll ``GET session`` until at least ``min_count`` matching responses."""
        deadline = time.monotonic() + timeout_sec
        last: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            session = await self.get_session(session_id)
            last = function_responses(session, name)
            if len(last) >= min_count:
                return last
            await asyncio.sleep(0.25)
        raise AssertionError(
            f"Timed out waiting for {min_count} {name!r} function responses; "
            f"last={last!r}\n{self.logs[-_LOG_TAIL_CHARS:]}"
        )


async def _drain_stream(proc: asyncio.subprocess.Process, chunks: list[str]) -> None:
    """Read combined stdout/stderr so the child cannot block on a full pipe."""
    stream = proc.stdout
    if stream is None:
        return
    while True:
        piece = await stream.read(4096)
        if not piece:
            break
        chunks.append(piece.decode("utf-8", errors="replace"))
        if sum(len(item) for item in chunks) > 256_000:
            joined = "".join(chunks)
            chunks.clear()
            chunks.append(joined[-128_000:])


async def _wait_until_serving(
    base_url: str,
    proc: asyncio.subprocess.Process,
    log_chunks: list[str],
) -> None:
    """Poll ``GET /list-apps`` until the API server is up."""
    deadline = time.monotonic() + _START_TIMEOUT_SEC
    last_error = ""
    async with httpx.AsyncClient() as probe:
        while time.monotonic() < deadline:
            if proc.returncode is not None:
                logs = "".join(log_chunks)
                raise AssertionError(
                    f"adk api_server exited with {proc.returncode} before serving.\n"
                    f"{logs[-_LOG_TAIL_CHARS:]}"
                )
            try:
                response = await probe.get(f"{base_url}/list-apps", timeout=1.0)
                if response.status_code == 200:
                    return
                last_error = f"HTTP {response.status_code}: {response.text[:500]}"
            except httpx.HTTPError as exc:
                last_error = str(exc)
            await asyncio.sleep(0.15)
    logs = "".join(log_chunks)
    raise AssertionError(
        f"adk api_server did not become ready within {_START_TIMEOUT_SEC:g}s "
        f"({last_error}).\n{logs[-_LOG_TAIL_CHARS:]}"
    )


@asynccontextmanager
async def run_adk_api_server(*, mcp_url: str, gcs_stub_dir: Path) -> AsyncIterator[AdkApiServer]:
    """Start ``python -m google.adk.cli api_server`` against this repo's agent folder."""
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["AUDIO_CONVERSATION_MCP_URL"] = mcp_url
    env["AUDIO_CONVERSATION_JOB_POLL_INTERVAL_SEC"] = "0.2"
    env["AUDIO_CONVERSATION_JOB_POLL_TIMEOUT_SEC"] = "60"
    env[GCS_STUB_DIR_ENV] = str(gcs_stub_dir)
    env["PYTHONUNBUFFERED"] = "1"
    command = [
        sys.executable,
        "-m",
        "google.adk.cli",
        "api_server",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--no-reload",
        "--log_level",
        "WARNING",
        "--session_service_uri",
        "memory://",
        "--artifact_service_uri",
        "memory://",
        str(_agents_dir()),
    ]
    log_chunks: list[str] = []
    proc = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(repo_root()),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    drain = asyncio.create_task(_drain_stream(proc, log_chunks))
    client = httpx.AsyncClient(timeout=30.0)
    try:
        await _wait_until_serving(base_url, proc, log_chunks)
        yield AdkApiServer(
            base_url=base_url,
            log_chunks=log_chunks,
            _client=client,
            _proc=proc,
            _drain=drain,
        )
    finally:
        await client.aclose()
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except TimeoutError:
                proc.kill()
                await proc.wait()
        if not drain.done():
            drain.cancel()
            try:
                await drain
            except asyncio.CancelledError:
                pass
