"""Streamable HTTP client for MCP tools the LLM must not call directly."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from fastmcp import Client

from tts_audio_conversation.logic.exceptions import ScriptPayloadError
from tts_audio_conversation.logic.models import ConversationScript
from tts_audio_conversation.logic.storage import download_bytes as gcs_download_bytes

from .config import AgentSettings

GCS_STUB_DIR_ENV = "AUDIO_CONVERSATION_ADK_GCS_STUB_DIR"

LLM_VISIBLE_MCP_TOOLS = (
    "validate_script",
    "get_conversation_status",
    "cancel_conversation",
)
EXCLUDED_MCP_TOOLS = (
    "start_conversation",
    "upload_script",
    "translate_script",
)

_RETRYABLE_TYPE_NAMES = frozenset(
    {
        "ConnectError",
        "ConnectTimeout",
        "ReadError",
        "ReadTimeout",
        "WriteError",
        "TimeoutException",
        "RemoteProtocolError",
        "NetworkError",
        "OSError",
        "ConnectionError",
        "ConnectionRefusedError",
        "ConnectionResetError",
    }
)


class McpClientError(RuntimeError):
    """Raised when an MCP tool call fails or returns an unexpected shape."""


DownloadBytesFn = Callable[[str], bytes | None]


def unwrap_tool_data(result: object) -> dict[str, Any]:
    """Unwrap a FastMCP ``CallToolResult`` (or similar) into a plain dict."""
    data = getattr(result, "data", None)
    parsed = _as_dict(data)
    if parsed is not None:
        return parsed
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        inner = structured.get("result", structured)
        parsed = _as_dict(inner)
        if parsed is not None:
            return parsed
    content = getattr(result, "content", None)
    if content:
        text = getattr(content[0], "text", None)
        if isinstance(text, str):
            loaded: Any = json.loads(text)
            parsed = _as_dict(loaded)
            if parsed is not None:
                return parsed
    raise McpClientError(f"Cannot extract tool data from {result!r}")


def language_code_from_script(text: str) -> str:
    """Read ``metadata.language_code`` from YAML/JSON, or ``und`` if unparsable."""
    try:
        script = ConversationScript.from_payload(text)
    except (ScriptPayloadError, ValueError, TypeError):
        return "und"
    code = (script.metadata.language_code or "").strip()
    return code or "und"


def is_retryable_mcp_error(exc: BaseException) -> bool:
    """Return True for transient HTTP/connect failures while MCP is starting."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if type(current).__name__ in _RETRYABLE_TYPE_NAMES:
            return True
        current = current.__cause__ or current.__context__
    return False


def gcs_stub_blob_path(stub_dir: Path | str, gcs_uri: str) -> Path:
    """Return the on-disk path used by ``AUDIO_CONVERSATION_ADK_GCS_STUB_DIR``."""
    digest = hashlib.sha256(gcs_uri.encode("utf-8")).hexdigest()
    return Path(stub_dir) / digest


def download_gcs_bytes(gcs_uri: str) -> bytes | None:
    """Download a ``gs://`` object with ADC (WAV fetch after a succeeded job).

    When ``AUDIO_CONVERSATION_ADK_GCS_STUB_DIR`` is set (ADK API-server tests),
    read mirrored FakeGcs blobs from that directory instead of calling GCS.
    """
    stub_dir = os.environ.get(GCS_STUB_DIR_ENV, "").strip()
    if stub_dir:
        path = gcs_stub_blob_path(stub_dir, gcs_uri)
        if path.is_file():
            return path.read_bytes()
        return None
    from google.cloud import storage  # type: ignore[attr-defined]

    return gcs_download_bytes(storage.Client(), gcs_uri)


def _as_dict(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        dumped = dump(mode="json")
        if isinstance(dumped, dict):
            return {str(key): _jsonable(item) for key, item in dumped.items()}
    return None


def _jsonable(value: object) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    iso = getattr(value, "isoformat", None)
    if callable(iso) and not isinstance(value, (str, bytes, int, float, bool)):
        return iso()
    return value


class McpConversationClient:
    """Call MCP tools over Streamable HTTP; one short-lived session per RPC."""

    def __init__(
        self,
        url: str,
        *,
        connect_attempts: int = 15,
        connect_retry_delay_sec: float = 0.4,
        download_bytes_fn: DownloadBytesFn | None = None,
    ) -> None:
        """Bind the MCP URL and optional GCS download override.

        Args:
            url: Streamable HTTP URL, typically ``http://127.0.0.1:8000/mcp``.
            connect_attempts: Retries when the MCP process is still starting.
            connect_retry_delay_sec: Base delay between connect retries.
            download_bytes_fn: Injected ``gs://`` downloader (tests / fake GCS).
        """
        self._url = url
        self._connect_attempts = max(1, connect_attempts)
        self._connect_retry_delay_sec = connect_retry_delay_sec
        self._download_bytes_fn = download_bytes_fn or download_gcs_bytes

    @property
    def url(self) -> str:
        """Return the configured MCP Streamable HTTP URL."""
        return self._url

    async def upload_script(self, script: str) -> dict[str, Any]:
        """Upload inline YAML/JSON and return ``script_id`` / ``script_uri``."""
        return await self.call_tool("upload_script", {"script": script})

    async def validate_script(self, script_uri: str) -> dict[str, Any]:
        """Soft-validate a ``gs://`` script URI."""
        return await self.call_tool("validate_script", {"script_uri": script_uri})

    async def start_conversation(self, script_uri: str) -> dict[str, Any]:
        """Start synthesis and return immediately (``queued`` / ``running``)."""
        started = await self.call_tool("start_conversation", {"script_uri": script_uri})
        return {
            "job_id": str(started.get("job_id") or ""),
            "status": str(started.get("status") or "queued"),
            "script_uri": str(started.get("script_uri") or script_uri),
            "audio_uri": str(started.get("audio_uri") or ""),
            "status_uri": str(started.get("status_uri") or ""),
            "error": started.get("error"),
        }

    async def get_conversation_status(self, job_id: str) -> dict[str, Any]:
        """Return the current job snapshot."""
        return await self.call_tool("get_conversation_status", {"job_id": job_id})

    async def cancel_conversation(self, job_id: str) -> dict[str, Any]:
        """Request cooperative cancel for a queued or running job."""
        return await self.call_tool("cancel_conversation", {"job_id": job_id})

    def download_audio(self, audio_uri: str) -> bytes | None:
        """Fetch WAV bytes from GCS (or the injected downloader)."""
        return self._download_bytes_fn(audio_uri)

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Call an MCP tool, retrying while the HTTP server is coming up."""
        payload = dict(arguments)
        last_exc: Exception | None = None
        delay = self._connect_retry_delay_sec
        for attempt in range(self._connect_attempts):
            try:
                async with Client(self._url) as client:
                    result = await client.call_tool(name, payload)
                return unwrap_tool_data(result)
            except McpClientError:
                raise
            except Exception as exc:
                last_exc = exc
                if not is_retryable_mcp_error(exc) or attempt + 1 >= self._connect_attempts:
                    raise McpClientError(f"MCP tool '{name}' failed: {exc}") from exc
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, 2.0)
        raise McpClientError(f"MCP tool '{name}' failed: {last_exc}") from last_exc


_client_override: McpConversationClient | None = None


def configure_mcp_client(client: McpConversationClient | None) -> None:
    """Install or clear the process-wide MCP client used by function tools."""
    global _client_override
    _client_override = client


def get_mcp_client() -> McpConversationClient:
    """Return the override client, or one built from ``AgentSettings``."""
    if _client_override is not None:
        return _client_override
    settings = AgentSettings()
    return McpConversationClient(settings.audio_conversation_mcp_url)
