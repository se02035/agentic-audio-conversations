"""Unit tests for the LRO resume plugin (no Gemini, no in-tool wait)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tts_audio_conversation.adk.config import AgentSettings
from tts_audio_conversation.adk.job_poll import JobPollTimeout
from tts_audio_conversation.adk.mcp_client import McpClientError
from tts_audio_conversation.logic.jobs.models import JobStatus

pytest.importorskip("google.adk")

from tts_audio_conversation.adk.create_audio import CREATE_AUDIO_TOOL_NAME  # noqa: E402
from tts_audio_conversation.adk.lro_plugin import ConversationJobClientPlugin  # noqa: E402


class _StubClient:
    """MCP client stub for plugin payload tests."""

    def __init__(self, snapshot: dict[str, Any], wav: bytes | None = b"RIFFFAKE") -> None:
        self.snapshot = snapshot
        self.wav = wav

    async def get_conversation_status(self, job_id: str) -> dict[str, Any]:
        """Return the configured snapshot."""
        assert job_id == self.snapshot["job_id"]
        return self.snapshot

    def download_audio(self, audio_uri: str) -> bytes | None:
        """Return injected WAV bytes."""
        _ = audio_uri
        return self.wav


def _plugin(client: _StubClient, **kwargs: Any) -> ConversationJobClientPlugin:
    """Build a plugin with a stub MCP client."""
    settings = AgentSettings(
        audio_conversation_job_poll_interval_sec=0.01,
        audio_conversation_job_poll_timeout_sec=2.0,
        **kwargs,
    )
    return ConversationJobClientPlugin(settings=settings, client=client)  # type: ignore[arg-type]


async def test_terminal_payload_downloads_wav_on_success() -> None:
    """Succeeded jobs download WAV and name ``audio_{job}_{lang}.wav``."""
    client = _StubClient(
        {
            "job_id": "job-1",
            "status": JobStatus.succeeded.value,
            "script_uri": "gs://b/s.yaml",
            "audio_uri": "gs://b/a.wav",
            "error": None,
        }
    )
    plugin = _plugin(client)
    payload, wav, name = await plugin._terminal_payload(
        {"job_id": "job-1", "language_code": "en-US", "script_artifact": "script_abc.yaml"}
    )
    assert wav == b"RIFFFAKE"
    assert name == "audio_job-1_en-US.wav"
    assert payload["status"] == "succeeded"
    assert payload["audio_artifact"] == name


async def test_terminal_payload_timeout_becomes_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Poll timeout is returned as a failed FunctionResponse payload."""

    async def boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise JobPollTimeout("job-x", 1.0)

    monkeypatch.setattr("tts_audio_conversation.adk.lro_plugin.wait_for_terminal_job", boom)
    plugin = _plugin(_StubClient({"job_id": "job-x", "status": "queued"}))
    payload, wav, name = await plugin._terminal_payload({"job_id": "job-x"})
    assert wav is None
    assert name is None
    assert payload["status"] == "failed"
    assert "job-x" in str(payload["error"])


async def test_terminal_payload_mcp_error_becomes_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP status failures are returned as a failed FunctionResponse payload."""

    async def boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise McpClientError("MCP tool 'get_conversation_status' failed: down")

    monkeypatch.setattr("tts_audio_conversation.adk.lro_plugin.wait_for_terminal_job", boom)
    plugin = _plugin(_StubClient({"job_id": "job-x", "status": "queued"}))
    payload, wav, name = await plugin._terminal_payload({"job_id": "job-x"})
    assert wav is None
    assert name is None
    assert payload["status"] == "failed"
    assert "down" in str(payload["error"])


async def test_terminal_payload_download_exception_sets_download_error() -> None:
    """GCS/download failures stay on the FunctionResponse; resume is not aborted."""

    class _BoomClient(_StubClient):
        def download_audio(self, audio_uri: str) -> bytes | None:
            raise OSError(f"gcs down {audio_uri}")

    plugin = _plugin(
        _BoomClient(
            {
                "job_id": "job-1",
                "status": JobStatus.succeeded.value,
                "script_uri": "gs://b/s.yaml",
                "audio_uri": "gs://b/a.wav",
                "error": None,
            }
        )
    )
    payload, wav, name = await plugin._terminal_payload(
        {"job_id": "job-1", "language_code": "en-US"}
    )
    assert wav is None
    assert name is None
    assert payload["status"] == "succeeded"
    assert "audio_artifact" not in payload
    assert "gcs down" in str(payload["download_error"])
    assert "gs://b/a.wav" in str(payload["download_error"])


async def test_after_tool_callback_records_function_call_id() -> None:
    """Queued start results are stored with the original function_call id."""
    plugin = _plugin(_StubClient({"job_id": "j"}))
    tool = MagicMock()
    tool.name = CREATE_AUDIO_TOOL_NAME
    ctx = MagicMock()
    ctx.function_call_id = "fc-99"
    ctx.state = {}
    result = {"job_id": "j9", "status": "queued", "script_uri": "gs://b/s", "audio_uri": "gs://b/a"}
    await plugin.after_tool_callback(tool=tool, tool_args={}, tool_context=ctx, result=result)
    pending = ctx.state["pending_audio_lro"]
    assert pending["function_call_id"] == "fc-99"
    assert pending["job_id"] == "j9"


async def test_after_run_starts_poller_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Background resume is scheduled once per (session, job)."""
    calls: list[str] = []

    async def fake_poll(
        self: ConversationJobClientPlugin,
        _ctx: Any,
        pending: dict[str, Any],
    ) -> None:
        calls.append(str(pending["job_id"]))

    monkeypatch.setattr(ConversationJobClientPlugin, "_poll_and_resume", fake_poll)
    plugin = _plugin(_StubClient({"job_id": "j1"}))
    ctx = MagicMock()
    ctx.session.id = "sess-1"
    ctx.session.state = {
        "pending_audio_lro": {
            "job_id": "j1",
            "function_call_id": "fc-1",
            "status": "queued",
        }
    }
    await plugin.after_run_callback(invocation_context=ctx)
    await plugin.after_run_callback(invocation_context=ctx)
    await plugin.wait_inflight(timeout_sec=2)
    assert calls == ["j1"]


async def test_after_run_skips_terminal_and_incomplete_pending() -> None:
    """No poller when the job is already terminal or the function_call id is missing."""
    plugin = _plugin(_StubClient({"job_id": "j1"}))
    ctx = MagicMock()
    ctx.session.id = "sess-2"
    ctx.session.state = {
        "pending_audio_lro": {
            "job_id": "j1",
            "function_call_id": "fc-1",
            "status": "succeeded",
        }
    }
    await plugin.after_run_callback(invocation_context=ctx)
    assert plugin._tasks == set()
    ctx.session.state = {"pending_audio_lro": {"job_id": "j1", "status": "queued"}}
    await plugin.after_run_callback(invocation_context=ctx)
    assert plugin._tasks == set()


async def test_on_event_callback_copies_function_call_id() -> None:
    """Long-running function_call ids are copied into pending LRO state."""
    plugin = _plugin(_StubClient({"job_id": "j1"}))
    call = MagicMock()
    call.id = "fc-7"
    call.name = CREATE_AUDIO_TOOL_NAME
    part = MagicMock()
    part.function_call = call
    event = MagicMock()
    event.long_running_tool_ids = {"fc-7"}
    event.content.parts = [part]
    ctx = MagicMock()
    ctx.session.state = {"pending_audio_lro": {"job_id": "j1"}}
    await plugin.on_event_callback(invocation_context=ctx, event=event)
    assert ctx.session.state["pending_audio_lro"]["function_call_id"] == "fc-7"


async def test_save_wav_and_resume_send_matching_function_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resume run_async uses the original function_call id and saves WAV bytes."""
    captured: list[dict[str, Any]] = []

    class _FakeRunner:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def run_async(self, **kwargs: Any) -> Any:
            captured.append(kwargs)
            if False:
                yield None

    monkeypatch.setattr("tts_audio_conversation.adk.lro_plugin.Runner", _FakeRunner)
    plugin = _plugin(
        _StubClient(
            {
                "job_id": "job-1",
                "status": JobStatus.succeeded.value,
                "script_uri": "gs://b/s.yaml",
                "audio_uri": "gs://b/a.wav",
                "error": None,
            }
        )
    )
    app = MagicMock()
    plugin.bind_app(app)
    ctx = MagicMock()
    ctx.app_name = "adk"
    ctx.user_id = "user"
    ctx.session.id = "sess"
    ctx.session.state = {"audio_overviews": [], "latest_script_id": "newer-sid"}
    ctx.session_service = MagicMock()
    ctx.artifact_service = MagicMock()
    ctx.artifact_service.save_artifact = AsyncMock(return_value=1)
    ctx.memory_service = None
    ctx.credential_service = None
    pending = {
        "job_id": "job-1",
        "function_call_id": "fc-1",
        "script_id": "captured-sid",
        "script_uri": "gs://b/s.yaml",
        "script_artifact": "script_sid.yaml",
        "language_code": "en-US",
        "status": "queued",
    }
    await plugin._poll_and_resume(ctx, pending)
    saved = ctx.artifact_service.save_artifact.await_args.kwargs
    assert saved["filename"] == "audio_job-1_en-US.wav"
    message = captured[0]["new_message"]
    response = message.parts[0].function_response
    assert response.id == "fc-1"
    assert response.name == CREATE_AUDIO_TOOL_NAME
    assert response.response["status"] == "succeeded"
    pending_state = captured[0]["state_delta"]["pending_audio_lro"]
    assert pending_state["audio_artifact"] == "audio_job-1_en-US.wav"
    assert pending_state["audio_artifact_version"] == 1
    overview = captured[0]["state_delta"]["audio_overviews"][-1]
    assert overview["script_id"] == "captured-sid"
    assert overview["script_uri"] == "gs://b/s.yaml"
    assert overview["script_artifact"] == "script_sid.yaml"


async def test_before_agent_copies_wav_version_to_artifact_delta() -> None:
    """ADK Web Artifacts tab reads artifactDelta; resume stashes the WAV version."""
    plugin = _plugin(_StubClient({"job_id": "j"}))
    ctx = MagicMock()
    ctx.state = {
        "pending_audio_lro": {
            "job_id": "job-1",
            "status": "succeeded",
            "audio_artifact": "audio_job-1_en-US.wav",
            "audio_artifact_version": 0,
        }
    }
    ctx.actions.artifact_delta = {}
    result = await plugin.before_agent_callback(agent=MagicMock(), callback_context=ctx)
    assert result is None
    assert ctx.actions.artifact_delta["audio_job-1_en-US.wav"] == 0
    assert "audio_artifact_version" not in ctx.state["pending_audio_lro"]


async def test_before_agent_skips_when_wav_version_missing() -> None:
    """Queued turns must not emit a spurious artifactDelta."""
    plugin = _plugin(_StubClient({"job_id": "j"}))
    ctx = MagicMock()
    ctx.state = {"pending_audio_lro": {"job_id": "job-1", "status": "queued"}}
    ctx.actions.artifact_delta = {}
    result = await plugin.before_agent_callback(agent=MagicMock(), callback_context=ctx)
    assert result is None
    assert ctx.actions.artifact_delta == {}


async def test_plugin_close_cancels_tasks() -> None:
    """close() cancels inflight resume tasks."""
    import asyncio

    plugin = _plugin(_StubClient({"job_id": "j"}))

    async def _never() -> None:
        await asyncio.sleep(60)

    task = asyncio.create_task(_never())
    plugin._tasks.add(task)
    await plugin.close()
    assert plugin._tasks == set()
    assert task.cancelled() or task.done()
