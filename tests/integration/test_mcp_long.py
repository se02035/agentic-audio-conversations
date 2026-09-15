"""Live MCP HTTP tests for the ~15-minute unicorn and German AI templates."""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

import pytest
from fastmcp import Client

from tests.integration.mcp_live import (
    LONG_POLL_TIMEOUT_SEC,
    START_DEADLINE_SEC,
    assert_mono_wav,
    delete_gcs_blob,
    live_mcp_http,
    tool_data,
    wav_sample_rate,
)
from tts_podcast_creator.logic.models import PodcastScript
from tts_podcast_creator.logic.storage import download_file
from tts_podcast_creator.mcp.jobs import JobStatus

_REPO_ROOT = Path(__file__).resolve().parents[2]
_UNICORN = _REPO_ROOT / "templates" / "unicorn_fairytale.yaml"
_GERMAN_AI = _REPO_ROOT / "templates" / "ai_software_engineering_de.yaml"


def _load_unicorn() -> PodcastScript:
    """Load and check the single-speaker fairytale template."""
    assert _UNICORN.exists(), f"Static template not found at {_UNICORN}"
    script = PodcastScript.from_path(_UNICORN)
    assert len(script.voices) == 1, f"Expected 1 speaker, found {len(script.voices)}"
    assert "narrator" in script.voices
    total_words = sum(len(turn.text.split()) for turn in script.turns)
    assert 2000 <= total_words <= 2500, f"Expected ~2,150 words, got {total_words}"
    return script


def _load_german_ai() -> PodcastScript:
    """Load and check the 2-speaker German AI podcast template."""
    assert _GERMAN_AI.exists(), f"Static template not found at {_GERMAN_AI}"
    script = PodcastScript.from_path(_GERMAN_AI)
    assert len(script.voices) == 2, f"Expected 2 speakers, found {len(script.voices)}"
    assert "moderatorin" in script.voices
    assert "experte" in script.voices
    assert "Aoede" in script.voices["moderatorin"].name
    assert "Fenrir" in script.voices["experte"].name
    total_words = sum(len(turn.text.split()) for turn in script.turns)
    assert 2000 <= total_words <= 2500, f"Expected ~2,150 words, got {total_words}"
    return script


@pytest.mark.integration
@pytest.mark.slow
async def test_live_mcp_long_form_unicorn_and_german_ai(tmp_path: Path) -> None:
    """Run both ~15-minute templates concurrently through MCP start/status/GCS download.

    start_podcast must return immediately for each job. Both asyncio tasks stay live,
    statuses are polled independently, and each downloaded WAV is longer than 300s.
    """
    unicorn = _load_unicorn()
    german = _load_german_ai()
    suffix = uuid.uuid4().hex[:8]
    prefix = f"podcasts/live_mcp_long_{suffix}"
    uris: list[str] = []

    async with live_mcp_http(prefix, max_concurrent_jobs=2) as live:
        try:
            async with Client(live.url) as client:
                valid_u = tool_data(
                    await client.call_tool("validate_script", {"script": unicorn.to_yaml()})
                )
                valid_g = tool_data(
                    await client.call_tool("validate_script", {"script": german.to_yaml()})
                )
                assert valid_u["valid"] is True
                assert valid_g["valid"] is True

                t0 = time.perf_counter()
                job_u = tool_data(
                    await client.call_tool("start_podcast", {"script": unicorn.to_yaml()})
                )
                job_g = tool_data(
                    await client.call_tool("start_podcast", {"script": german.to_yaml()})
                )
                elapsed = time.perf_counter() - t0
                assert elapsed < START_DEADLINE_SEC, (
                    f"long-form start_podcast blocked for {elapsed:.2f}s; "
                    f"expected < {START_DEADLINE_SEC}s"
                )
                assert job_u["job_id"] != job_g["job_id"]
                assert job_u["status"] in {JobStatus.queued, JobStatus.running}
                assert job_g["status"] in {JobStatus.queued, JobStatus.running}
                uris.extend(
                    [
                        job_u["audio_uri"],
                        job_u["status_uri"],
                        job_g["audio_uri"],
                        job_g["status_uri"],
                    ]
                )

                live_tasks = [task for task in live.manager._tasks.values() if not task.done()]
                assert len(live_tasks) == 2

                saw_both_active = False
                deadline = time.monotonic() + LONG_POLL_TIMEOUT_SEC
                status_u = job_u
                status_g = job_g
                while time.monotonic() < deadline:
                    status_u = tool_data(
                        await client.call_tool("get_podcast_status", {"job_id": job_u["job_id"]})
                    )
                    status_g = tool_data(
                        await client.call_tool("get_podcast_status", {"job_id": job_g["job_id"]})
                    )
                    active = {JobStatus.queued, JobStatus.running}
                    if status_u["status"] in active and status_g["status"] in active:
                        saw_both_active = True
                    if (
                        status_u["status"] == JobStatus.succeeded
                        and status_g["status"] == JobStatus.succeeded
                    ):
                        break
                    if status_u["status"] in {JobStatus.failed, JobStatus.cancelled}:
                        raise AssertionError(
                            f"unicorn job ended {status_u['status']}: {status_u.get('error')}"
                        )
                    if status_g["status"] in {JobStatus.failed, JobStatus.cancelled}:
                        raise AssertionError(
                            f"german AI job ended {status_g['status']}: {status_g.get('error')}"
                        )
                    await asyncio.sleep(1.0)
                else:
                    raise AssertionError(
                        f"long-form jobs did not both succeed: "
                        f"{status_u['status']}/{status_g['status']}"
                    )

                assert saw_both_active
                assert status_u["status"] == JobStatus.succeeded
                assert status_g["status"] == JobStatus.succeeded

            wav_u = tmp_path / f"mcp_unicorn_{suffix}.wav"
            wav_g = tmp_path / f"mcp_german_ai_{suffix}.wav"
            download_file(live.gcs_client, job_u["audio_uri"], wav_u)
            download_file(live.gcs_client, job_g["audio_uri"], wav_g)
            assert_mono_wav(wav_u, min_duration_secs=300)
            assert_mono_wav(wav_g, min_duration_secs=300)
            assert wav_sample_rate(wav_u) == unicorn.metadata.sample_rate_hertz
            assert wav_sample_rate(wav_g) == german.metadata.sample_rate_hertz
        finally:
            for uri in uris:
                delete_gcs_blob(live.gcs_client, uri)
