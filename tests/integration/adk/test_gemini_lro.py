"""ADK agent integration: live Gemini via real ``adk api_server`` + mocked MCP."""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import pytest
from dotenv import load_dotenv

load_dotenv()

pytest.importorskip("google.adk")
pytest.importorskip("httpx")

from google.auth.exceptions import DefaultCredentialsError  # noqa: E402

from tests.integration.adk.api_server import ADK_APP_NAME  # noqa: E402
from tests.integration.adk.stack import AdkAgentStack  # noqa: E402
from tests.unit.adk.api_events import (  # noqa: E402
    artifact_delta_filenames,
    function_call_names,
    function_responses,
    inline_file_part,
)
from tts_audio_conversation.adk.config import example_script_path  # noqa: E402
from tts_audio_conversation.adk.create_audio import CREATE_AUDIO_TOOL_NAME  # noqa: E402
from tts_audio_conversation.logic.jobs.models import JobStatus  # noqa: E402


def _require_gemini_adc() -> None:
    """Skip unless ADC + project are available for the configured Gemini model."""
    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        pytest.skip("GOOGLE_CLOUD_PROJECT must be set for ADK integration tests")
    try:
        import google.auth

        google.auth.default(scopes=("https://www.googleapis.com/auth/cloud-platform",))
    except DefaultCredentialsError:
        pytest.skip("Application Default Credentials are required for ADK integration tests")


def _response_payload(response: dict[str, Any]) -> dict[str, Any]:
    """Unwrap a functionResponse ``response`` field as a dict."""
    payload = response.get("response")
    return dict(payload) if isinstance(payload, dict) else {}


async def test_adk_agent_lro_returns_queued_before_job_succeeds(
    adk_agent_stack: AdkAgentStack,
) -> None:
    """Gemini drives ingest/validate/create over API-server REST; LRO stays queued."""
    _require_gemini_adc()
    api = adk_agent_stack.api
    yaml_bytes = example_script_path().read_bytes()
    apps = await api.list_apps()
    assert ADK_APP_NAME in apps
    session_id = f"s-{uuid.uuid4().hex[:12]}"
    created = await api.create_session(session_id)
    assert created.get("id") == session_id
    await api.save_artifact(
        session_id,
        "script.yaml",
        yaml_bytes,
        "application/yaml",
    )
    prompt = (
        "The YAML conversation script is already uploaded as a session "
        "artifact named script.yaml. Ingest it, validate_script, then "
        "create_audio_conversation now. Do not ask clarifying questions."
    )
    t0 = time.perf_counter()
    first_events = await api.run(
        session_id,
        prompt,
        inline_files=[
            inline_file_part("script.yaml", yaml_bytes, "application/yaml"),
        ],
    )
    first_elapsed = time.perf_counter() - t0
    call_names = function_call_names(first_events)
    assert "ingest_uploaded_script" in call_names
    assert "validate_script" in call_names
    assert CREATE_AUDIO_TOOL_NAME in call_names
    lro_responses = function_responses(first_events, CREATE_AUDIO_TOOL_NAME)
    assert lro_responses, "expected a long-running FunctionResponse from create"
    first_payload = _response_payload(lro_responses[0])
    assert first_payload.get("status") in {
        JobStatus.queued.value,
        JobStatus.running.value,
    }
    job_id = str(first_payload.get("job_id") or "")
    assert job_id
    assert first_elapsed < (adk_agent_stack.synth_delay_sec + 30)
    if first_elapsed < adk_agent_stack.synth_delay_sec:
        live_status = await adk_agent_stack.mcp.service.get_job(job_id)
        assert live_status.status in {JobStatus.queued, JobStatus.running}
    assert first_payload.get("status") != JobStatus.succeeded.value
    all_lro = await api.wait_for_function_responses(
        session_id,
        CREATE_AUDIO_TOOL_NAME,
        min_count=2,
        timeout_sec=60,
    )
    terminal = _response_payload(all_lro[-1])
    assert terminal.get("status") == JobStatus.succeeded.value
    assert all_lro[-1].get("id") == lro_responses[0].get("id")
    audio_name = str(terminal.get("audio_artifact") or "")
    assert audio_name.startswith("audio_")
    assert audio_name.endswith(".wav")
    assert audio_name != "audio.wav"
    keys = await api.list_artifacts(session_id)
    assert audio_name in keys
    session = await api.get_session(session_id)
    assert audio_name in artifact_delta_filenames(session)
