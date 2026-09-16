"""Unit tests for the ingest function tool (stub MCP, no api_server)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("google.adk")

from google.genai import types  # noqa: E402

from tests.unit.adk.fake_tool_context import FakeToolContext, yaml_part  # noqa: E402
from tts_audio_conversation.adk.artifact_ids import (  # noqa: E402
    LATEST_SCRIPT_ARTIFACT_KEY,
    LATEST_SCRIPT_ID_KEY,
    OVERVIEWS_STATE_KEY,
    script_artifact_name,
)
from tts_audio_conversation.adk.ingest import (  # noqa: E402
    ingest_uploaded_script,
    yaml_bytes_from_part,
)
from tts_audio_conversation.adk.mcp_client import configure_mcp_client  # noqa: E402


def test_yaml_bytes_from_inline_blob() -> None:
    """Inline blob bytes win over an empty text field."""
    part = types.Part(
        inline_data=types.Blob(mime_type="application/yaml", data=b"title: x"),
        text="",
    )
    assert yaml_bytes_from_part(part) == b"title: x"


def test_yaml_bytes_from_text_part() -> None:
    """Text-only artifacts are encoded as UTF-8."""
    assert yaml_bytes_from_part(types.Part(text="speaker: host")) == b"speaker: host"


def test_yaml_bytes_from_empty_part_raises() -> None:
    """Parts with neither blob nor text are rejected."""
    with pytest.raises(ValueError, match="no YAML"):
        yaml_bytes_from_part(types.Part())


async def test_ingest_reports_missing_yaml() -> None:
    """Ingest returns an error when the session has no YAML artifact."""
    configure_mcp_client(None)
    ctx = FakeToolContext({})
    result = await ingest_uploaded_script(ctx)  # type: ignore[arg-type]
    assert result["status"] == "error"
    assert "YAML" in result["error"]


async def test_ingest_reports_unloadable_artifact() -> None:
    """Listed YAML that fails to load is an error, not an upload."""
    configure_mcp_client(None)
    ctx = FakeToolContext({}, listed=["script.yaml"])
    result = await ingest_uploaded_script(ctx)  # type: ignore[arg-type]
    assert result["status"] == "error"
    assert "Could not load" in result["error"]


async def test_ingest_reports_empty_part() -> None:
    """An artifact with neither YAML bytes nor text is an ingest error."""
    configure_mcp_client(None)
    ctx = FakeToolContext({"script.yaml": types.Part()})
    result = await ingest_uploaded_script(ctx)  # type: ignore[arg-type]
    assert result["status"] == "error"
    assert "YAML" in result["error"] or "no YAML" in result["error"].lower()


async def test_ingest_reports_invalid_utf8() -> None:
    """Non-UTF-8 YAML bytes surface as an ingest error."""
    configure_mcp_client(None)
    ctx = FakeToolContext({"script.yaml": yaml_part(b"\xff\xfe")})
    result = await ingest_uploaded_script(ctx)  # type: ignore[arg-type]
    assert result["status"] == "error"


async def test_ingest_reports_incomplete_upload() -> None:
    """MCP upload without script_id/script_uri is an error."""
    client = AsyncMock()
    client.upload_script = AsyncMock(return_value={"script_id": "", "script_uri": ""})
    configure_mcp_client(client)
    try:
        ctx = FakeToolContext({"script.yaml": yaml_part(b"metadata: {}\n")})
        result = await ingest_uploaded_script(ctx)  # type: ignore[arg-type]
        assert result["status"] == "error"
        assert "script_id" in result["error"]
        assert not ctx.saved
    finally:
        configure_mcp_client(None)


async def test_ingest_uploads_and_saves_canonical_artifact(sample_script_yaml: str) -> None:
    """Successful ingest stores unique script artifact names and session state."""
    client = AsyncMock()
    client.upload_script = AsyncMock(
        return_value={"script_id": "sid-1", "script_uri": "gs://bucket/scripts/sid-1/script.yaml"}
    )
    configure_mcp_client(client)
    try:
        ctx = FakeToolContext({"script.yaml": yaml_part(sample_script_yaml.encode("utf-8"))})
        result = await ingest_uploaded_script(ctx)  # type: ignore[arg-type]
        assert result["status"] == "ok"
        assert result["script_id"] == "sid-1"
        assert result["script_artifact"] == script_artifact_name("sid-1")
        assert result["script_artifact"] != "script.yaml"
        assert result["language_code"] == "en-US"
        assert script_artifact_name("sid-1") in ctx.saved
        assert ctx.state[LATEST_SCRIPT_ID_KEY] == "sid-1"
        assert ctx.state[LATEST_SCRIPT_ARTIFACT_KEY] == script_artifact_name("sid-1")
        overviews = ctx.state[OVERVIEWS_STATE_KEY]
        assert overviews[0]["status"] == "ingested"
        client.upload_script.assert_awaited_once()
    finally:
        configure_mcp_client(None)


async def test_ingest_from_text_artifact(sample_script_yaml: str) -> None:
    """Chat YAML uploaded as a text part is still ingested."""
    client = AsyncMock()
    client.upload_script = AsyncMock(
        return_value={"script_id": "sid-text", "script_uri": "gs://b/s.yaml"}
    )
    configure_mcp_client(client)
    try:
        ctx = FakeToolContext({"notes.yml": types.Part(text=sample_script_yaml)})
        result = await ingest_uploaded_script(ctx)  # type: ignore[arg-type]
        assert result["status"] == "ok"
        assert result["script_id"] == "sid-text"
        assert result["language_code"] == "en-US"
    finally:
        configure_mcp_client(None)


async def test_two_ingests_mint_distinct_script_artifacts() -> None:
    """Each MCP script_id becomes its own ``script_{id}.yaml`` artifact."""
    uploads = [
        {"script_id": "aaa", "script_uri": "gs://b/a.yaml"},
        {"script_id": "bbb", "script_uri": "gs://b/b.yaml"},
    ]

    class _Client:
        async def upload_script(self, _text: str) -> dict[str, Any]:
            return uploads.pop(0)

    configure_mcp_client(_Client())  # type: ignore[arg-type]
    try:
        first = await ingest_uploaded_script(
            FakeToolContext({"script.yaml": yaml_part(b"a: 1")})  # type: ignore[arg-type]
        )
        second = await ingest_uploaded_script(
            FakeToolContext({"script.yaml": yaml_part(b"a: 2")})  # type: ignore[arg-type]
        )
        assert first["script_artifact"] != second["script_artifact"]
        assert first["script_id"] != second["script_id"]
    finally:
        configure_mcp_client(None)
