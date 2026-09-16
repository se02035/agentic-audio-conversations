"""Unit tests for the long-running create tool (stub MCP, no api_server)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("google.adk")

from tests.unit.adk.fake_tool_context import FakeToolContext  # noqa: E402
from tts_audio_conversation.adk.artifact_ids import (  # noqa: E402
    LATEST_LANGUAGE_CODE_KEY,
    LATEST_SCRIPT_ARTIFACT_KEY,
    LATEST_SCRIPT_ID_KEY,
    OVERVIEWS_STATE_KEY,
    PENDING_LRO_STATE_KEY,
    audio_artifact_name,
)
from tts_audio_conversation.adk.create_audio import (  # noqa: E402
    CREATE_AUDIO_TOOL_NAME,
    create_audio_conversation,
)
from tts_audio_conversation.adk.mcp_client import configure_mcp_client  # noqa: E402
from tts_audio_conversation.logic.jobs.models import JobStatus  # noqa: E402


async def test_create_returns_queued_and_records_pending_lro() -> None:
    """Start returns immediately; pending LRO state uses the function_call id."""
    client = AsyncMock()
    client.start_conversation = AsyncMock(
        return_value={
            "job_id": "job-9",
            "status": JobStatus.queued.value,
            "script_uri": "gs://b/s.yaml",
            "audio_uri": "",
        }
    )
    configure_mcp_client(client)
    try:
        ctx = FakeToolContext({}, function_call_id="fc-create")
        ctx.state[LATEST_LANGUAGE_CODE_KEY] = "en-US"
        ctx.state[LATEST_SCRIPT_ARTIFACT_KEY] = "script_sid.yaml"
        ctx.state[LATEST_SCRIPT_ID_KEY] = "sid"
        result = await create_audio_conversation("gs://b/s.yaml", ctx)  # type: ignore[arg-type]
        assert result["job_id"] == "job-9"
        assert result["status"] == JobStatus.queued.value
        assert result["status"] != JobStatus.succeeded.value
        pending = ctx.state[PENDING_LRO_STATE_KEY]
        assert pending["function_call_id"] == "fc-create"
        assert pending["tool_name"] == CREATE_AUDIO_TOOL_NAME
        assert pending["job_id"] == "job-9"
        overviews = ctx.state[OVERVIEWS_STATE_KEY]
        assert overviews[0]["job_id"] == "job-9"
        assert audio_artifact_name("job-9", "en-US") != "audio.wav"
        client.start_conversation.assert_awaited_once_with("gs://b/s.yaml")
    finally:
        configure_mcp_client(None)


async def test_create_defaults_language_when_state_empty() -> None:
    """Missing language in session state becomes ``und`` on the pending LRO."""
    client = AsyncMock()
    client.start_conversation = AsyncMock(
        return_value={
            "job_id": "job-und",
            "status": "queued",
            "script_uri": "gs://b/s.yaml",
            "audio_uri": None,
        }
    )
    configure_mcp_client(client)
    try:
        ctx = FakeToolContext({})
        result = await create_audio_conversation("gs://b/s.yaml", ctx)  # type: ignore[arg-type]
        assert result["audio_uri"] == ""
        assert ctx.state[PENDING_LRO_STATE_KEY]["language_code"] == "und"
    finally:
        configure_mcp_client(None)


async def test_two_creates_use_distinct_job_ids() -> None:
    """Each start call records its own job id (unique audio artifact later)."""
    jobs = ["job-a", "job-b"]

    class _Client:
        async def start_conversation(self, script_uri: str) -> dict[str, Any]:
            return {
                "job_id": jobs.pop(0),
                "status": "queued",
                "script_uri": script_uri,
                "audio_uri": "",
            }

    configure_mcp_client(_Client())  # type: ignore[arg-type]
    try:
        first = await create_audio_conversation(
            "gs://b/a.yaml",
            FakeToolContext({}, function_call_id="fc-a"),  # type: ignore[arg-type]
        )
        second = await create_audio_conversation(
            "gs://b/b.yaml",
            FakeToolContext({}, function_call_id="fc-b"),  # type: ignore[arg-type]
        )
        assert first["job_id"] != second["job_id"]
        assert audio_artifact_name(first["job_id"], "en-US") != audio_artifact_name(
            second["job_id"], "en-US"
        )
    finally:
        configure_mcp_client(None)
