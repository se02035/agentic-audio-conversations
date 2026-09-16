"""Live MCP HTTP tests for the ~15-minute unicorn and German AI templates."""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from tests.e2e.mcp_live import (
    LONG_POLL_TIMEOUT_SEC,
    START_DEADLINE_SEC,
    assert_mono_wav,
    delete_gcs_blob,
    live_mcp_http,
    tool_data,
    wav_sample_rate,
)
from tts_audio_conversation.logic.jobs.models import JobStatus
from tts_audio_conversation.logic.models import ConversationScript
from tts_audio_conversation.logic.storage import download_file

_REPO_ROOT = Path(__file__).resolve().parents[2]
_UNICORN = _REPO_ROOT / "templates" / "unicorn_fairytale.yaml"
_GERMAN_AI = _REPO_ROOT / "templates" / "ai_software_engineering_de.yaml"


def _load_unicorn() -> ConversationScript:
    """Load and check the single-speaker fairytale template."""
    assert _UNICORN.exists(), f"Static template not found at {_UNICORN}"
    script = ConversationScript.from_path(_UNICORN)
    assert len(script.voices) == 1, f"Expected 1 speaker, found {len(script.voices)}"
    assert "narrator" in script.voices
    total_words = sum(len(turn.text.split()) for turn in script.turns)
    assert 2000 <= total_words <= 2500, f"Expected ~2,150 words, got {total_words}"
    return script


def _load_german_ai() -> ConversationScript:
    """Load and check the 2-speaker German AI conversation template."""
    assert _GERMAN_AI.exists(), f"Static template not found at {_GERMAN_AI}"
    script = ConversationScript.from_path(_GERMAN_AI)
    assert len(script.voices) == 2, f"Expected 2 speakers, found {len(script.voices)}"
    assert "moderatorin" in script.voices
    assert "experte" in script.voices
    assert "Aoede" in script.voices["moderatorin"].name
    assert "Fenrir" in script.voices["experte"].name
    total_words = sum(len(turn.text.split()) for turn in script.turns)
    assert 2000 <= total_words <= 2500, f"Expected ~2,150 words, got {total_words}"
    return script


async def _run_long_mcp_job(
    *,
    label: str,
    script: ConversationScript,
    tmp_path: Path,
    prefix_stem: str,
) -> None:
    """Upload → start → poll → download one long-form MCP job."""
    suffix = uuid.uuid4().hex[:8]
    prefix = f"conversation/{prefix_stem}_{suffix}"
    uris: list[str] = []

    async with live_mcp_http(prefix, max_concurrent_jobs=1) as live:
        try:
            async with Client(live.url) as client:
                uploaded = tool_data(
                    await client.call_tool("upload_script", {"script": script.to_yaml()})
                )
                valid = tool_data(
                    await client.call_tool(
                        "validate_script", {"script_uri": uploaded["script_uri"]}
                    )
                )
                assert valid["valid"] is True

                t0 = time.perf_counter()
                started = tool_data(
                    await client.call_tool(
                        "start_conversation",
                        {"script_uri": uploaded["script_uri"]},
                    )
                )
                elapsed = time.perf_counter() - t0
                assert elapsed < START_DEADLINE_SEC, (
                    f"{label} start_conversation blocked for {elapsed:.2f}s; "
                    f"expected < {START_DEADLINE_SEC}s"
                )
                assert started["status"] in {JobStatus.queued, JobStatus.running}
                uris.extend([started["audio_uri"], started["status_uri"], uploaded["script_uri"]])

                status: dict[str, Any] = started
                deadline = time.monotonic() + LONG_POLL_TIMEOUT_SEC
                while time.monotonic() < deadline:
                    status = tool_data(
                        await client.call_tool(
                            "get_conversation_status",
                            {"job_id": started["job_id"]},
                        )
                    )
                    if status["status"] == JobStatus.succeeded:
                        break
                    if status["status"] in {JobStatus.failed, JobStatus.cancelled}:
                        raise AssertionError(
                            f"{label} job ended {status['status']}: {status.get('error')}"
                        )
                    await asyncio.sleep(1.0)
                else:
                    raise AssertionError(f"{label} job did not succeed; last={status['status']}")

            wav_path = tmp_path / f"mcp_{prefix_stem}_{suffix}.wav"
            download_file(live.gcs_client, started["audio_uri"], wav_path)
            assert_mono_wav(wav_path, min_duration_secs=300)
            assert wav_sample_rate(wav_path) == script.metadata.sample_rate_hertz
        finally:
            for uri in uris:
                delete_gcs_blob(live.gcs_client, uri)


@pytest.mark.e2e
@pytest.mark.slow
async def test_live_mcp_long_form_unicorn_single_speaker(tmp_path: Path) -> None:
    """Single-speaker Companion path: ~15-minute unicorn fairytale via MCP."""
    await _run_long_mcp_job(
        label="unicorn",
        script=_load_unicorn(),
        tmp_path=tmp_path,
        prefix_stem="live_mcp_long_unicorn",
    )


@pytest.mark.e2e
@pytest.mark.slow
async def test_live_mcp_long_form_german_ai_two_speaker(tmp_path: Path) -> None:
    """Two-speaker Chirp 3 HD path: ~15-minute German AI dialogue via MCP."""
    await _run_long_mcp_job(
        label="german_ai",
        script=_load_german_ai(),
        tmp_path=tmp_path,
        prefix_stem="live_mcp_long_german_ai",
    )
