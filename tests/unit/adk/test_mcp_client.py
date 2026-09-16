"""Unit tests for the FastMCP HTTP client used by the ADK adapter."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.unit.mcp.helpers import FakeGcs
from tts_audio_conversation.adk.mcp_client import (
    EXCLUDED_MCP_TOOLS,
    GCS_STUB_DIR_ENV,
    LLM_VISIBLE_MCP_TOOLS,
    McpClientError,
    McpConversationClient,
    configure_mcp_client,
    download_gcs_bytes,
    gcs_stub_blob_path,
    get_mcp_client,
    is_retryable_mcp_error,
    language_code_from_script,
    unwrap_tool_data,
)


def test_llm_visible_tools_exclude_start_and_upload() -> None:
    """The model must not see start/upload/translate MCP tools."""
    visible = set(LLM_VISIBLE_MCP_TOOLS)
    excluded = set(EXCLUDED_MCP_TOOLS)
    assert visible.isdisjoint(excluded)
    assert visible == {
        "validate_script",
        "get_conversation_status",
        "cancel_conversation",
    }


def test_unwrap_tool_data_from_model_and_json_text() -> None:
    """Support FastMCP structured models and JSON content fallbacks."""
    model = SimpleNamespace(model_dump=lambda mode="python": {"job_id": "j1", "status": "queued"})
    assert unwrap_tool_data(SimpleNamespace(data=model)) == {"job_id": "j1", "status": "queued"}
    text_result = SimpleNamespace(
        data=None,
        structured_content=None,
        content=[SimpleNamespace(text='{"ok": true}')],
    )
    assert unwrap_tool_data(text_result) == {"ok": True}
    with pytest.raises(McpClientError):
        unwrap_tool_data(SimpleNamespace(data=None, structured_content=None, content=None))


def test_language_code_from_script(sample_script_yaml: str) -> None:
    """Parse BCP-47 from valid YAML; unparsable payloads become ``und``."""
    assert language_code_from_script(sample_script_yaml) == "en-US"
    assert language_code_from_script("not: [yaml") == "und"


def test_is_retryable_mcp_error_walks_cause() -> None:
    """Connection failures (including nested causes) are retryable."""
    assert is_retryable_mcp_error(ConnectionRefusedError("down"))
    wrapped = RuntimeError("boom")
    wrapped.__cause__ = ConnectionError("refused")
    assert is_retryable_mcp_error(wrapped)
    assert not is_retryable_mcp_error(ValueError("nope"))


def test_unwrap_nested_jsonable_and_url_property() -> None:
    """Nested lists and the configured MCP URL are preserved."""
    payload = unwrap_tool_data(SimpleNamespace(data={"items": [{"n": 1}], "ok": True}))
    assert payload == {"items": [{"n": 1}], "ok": True}
    client = McpConversationClient("http://127.0.0.1:8000/mcp")
    assert client.url.endswith("/mcp")


async def test_call_tool_maps_non_retryable_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-retryable failures become ``McpClientError`` without extra attempts."""

    class _Boom:
        def __init__(self, _url: str) -> None:
            pass

        async def __aenter__(self) -> _Boom:
            raise ValueError("bad tool")

        async def __aexit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr("tts_audio_conversation.adk.mcp_client.Client", _Boom)
    client = McpConversationClient("http://127.0.0.1:9/mcp", connect_attempts=3)
    with pytest.raises(McpClientError, match="bad tool"):
        await client.cancel_conversation("job-1")


def test_download_gcs_bytes_reads_stub_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """API-server tests mirror FakeGcs blobs into a stub dir the child process reads."""
    uri = "gs://test-eu-bucket/jobs/j1/output/audio.wav"
    store = FakeGcs(persist_dir=tmp_path)
    store.upload_bytes(None, uri, b"RIFFWAVE", "audio/wav")
    assert gcs_stub_blob_path(tmp_path, uri).is_file()
    monkeypatch.setenv(GCS_STUB_DIR_ENV, str(tmp_path))
    assert download_gcs_bytes(uri) == b"RIFFWAVE"
    assert download_gcs_bytes("gs://missing/nope.wav") is None


async def test_call_tool_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Transient connect errors retry until a successful FastMCP call."""
    attempts = {"n": 0}

    class _FakeClient:
        def __init__(self, _url: str) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ConnectionRefusedError("not yet")
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def call_tool(self, name: str, payload: dict[str, str]) -> MagicMock:
            assert name == "validate_script"
            assert payload == {"script_uri": "gs://b/s.yaml"}
            result = MagicMock()
            result.data = {"valid": True}
            result.structured_content = None
            result.content = None
            return result

    monkeypatch.setattr("tts_audio_conversation.adk.mcp_client.Client", _FakeClient)
    client = McpConversationClient("http://127.0.0.1:9/mcp", connect_retry_delay_sec=0.0)
    data = await client.validate_script("gs://b/s.yaml")
    assert data == {"valid": True}
    assert attempts["n"] == 3


def test_configure_mcp_client_override_and_clear() -> None:
    """Function tools use the installed client until it is cleared."""
    client = McpConversationClient("http://127.0.0.1:9/mcp")
    configure_mcp_client(client)
    try:
        assert get_mcp_client() is client
    finally:
        configure_mcp_client(None)
    built = get_mcp_client()
    assert built is not client
    assert built.url.endswith("/mcp")


def test_get_mcp_client_reads_settings_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no override, the client URL comes from ``AgentSettings``."""
    configure_mcp_client(None)
    monkeypatch.setenv("AUDIO_CONVERSATION_MCP_URL", "http://127.0.0.1:4321/mcp")
    client = get_mcp_client()
    assert client.url == "http://127.0.0.1:4321/mcp"
