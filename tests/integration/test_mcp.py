"""Live MCP HTTP tests: start → status poll → GCS download, including two concurrent jobs."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

import pytest
from fastmcp import Client

from tests.integration.mcp_live import (
    SHORT_POLL_TIMEOUT_SEC,
    START_DEADLINE_SEC,
    assert_mono_wav,
    delete_gcs_blob,
    live_mcp_http,
    tool_data,
)
from tts_podcast_creator.logic.models import (
    DialogueTurn,
    PodcastMetadata,
    PodcastScript,
    VoiceConfig,
)
from tts_podcast_creator.logic.storage import download_file
from tts_podcast_creator.mcp.jobs import JobStatus


def _smoke_script_yaml(*, title: str, line: str) -> str:
    """Tiny script so a live MCP job finishes in seconds, not minutes."""
    script = PodcastScript(
        metadata=PodcastMetadata(title=title, language_code="en-US"),
        voices={
            "host": VoiceConfig(name="en-US-Chirp3-HD-Fenrir", language_code="en-US"),
        },
        turns=[
            DialogueTurn(speaker="host", text=line),
            DialogueTurn(speaker="host", text="Chirp 3 HD audio should land in GCS."),
        ],
    )
    return script.to_yaml()


@pytest.mark.integration
async def test_live_mcp_flow_start_status_gcs_download(tmp_path: Path) -> None:
    """HTTP flow: start_podcast, poll get_podcast_status, download WAV + status.json from GCS."""
    suffix = uuid.uuid4().hex[:8]
    prefix = f"podcasts/live_mcp_flow_{suffix}"
    uris: list[str] = []
    async with live_mcp_http(prefix) as live:
        payload = _smoke_script_yaml(
            title="MCP Flow Smoke",
            line="This is a live MCP start, status, and download integration test.",
        )
        try:
            async with Client(live.url) as client:
                tools = {tool.name for tool in await client.list_tools()}
                assert "download" not in tools
                assert "start_podcast" in tools
                assert "get_podcast_status" in tools

                valid = tool_data(await client.call_tool("validate_script", {"script": payload}))
                assert valid["valid"] is True

                t0 = time.perf_counter()
                started = tool_data(await client.call_tool("start_podcast", {"script": payload}))
                elapsed = time.perf_counter() - t0
                assert elapsed < START_DEADLINE_SEC, (
                    f"start_podcast blocked for {elapsed:.2f}s; expected < {START_DEADLINE_SEC}s"
                )
                assert started["status"] in {JobStatus.queued, JobStatus.running}
                uris.extend([started["audio_uri"], started["status_uri"]])
                assert started["audio_uri"].startswith(f"gs://{live.bucket}/{prefix}/")
                assert started["audio_uri"].endswith("/audio.wav")
                assert started["status_uri"].endswith("/status.json")

                seen: set[str] = {started["status"]}
                deadline = time.monotonic() + SHORT_POLL_TIMEOUT_SEC
                status = started
                while time.monotonic() < deadline:
                    status = tool_data(
                        await client.call_tool("get_podcast_status", {"job_id": started["job_id"]})
                    )
                    seen.add(status["status"])
                    if status["status"] in {
                        JobStatus.succeeded,
                        JobStatus.failed,
                        JobStatus.cancelled,
                    }:
                        break
                    await asyncio.sleep(0.25)
                assert status["status"] == JobStatus.succeeded, (
                    f"MCP job ended {status['status']}: {status.get('error')}"
                )
                assert seen & {JobStatus.queued, JobStatus.running}

            wav_path = tmp_path / f"mcp_flow_{suffix}.wav"
            status_path = tmp_path / f"mcp_flow_{suffix}_status.json"
            download_file(live.gcs_client, started["audio_uri"], wav_path)
            download_file(live.gcs_client, started["status_uri"], status_path)
            assert_mono_wav(wav_path)
            sidecar = json.loads(status_path.read_text(encoding="utf-8"))
            assert sidecar["status"] == JobStatus.succeeded
            assert sidecar["job_id"] == started["job_id"]
        finally:
            for uri in uris:
                delete_gcs_blob(live.gcs_client, uri)


@pytest.mark.integration
async def test_live_mcp_two_concurrent_jobs_status_and_download(tmp_path: Path) -> None:
    """Two HTTP start_podcast calls overlap; each job is polled and downloaded separately."""
    suffix = uuid.uuid4().hex[:8]
    prefix = f"podcasts/live_mcp_conc_{suffix}"
    uris: list[str] = []
    async with live_mcp_http(prefix) as live:
        script_a = _smoke_script_yaml(
            title="MCP Concurrent Job A",
            line="This is concurrent MCP job alpha running in the EU.",
        )
        script_b = _smoke_script_yaml(
            title="MCP Concurrent Job B",
            line="This is concurrent MCP job bravo running in the EU.",
        )
        try:
            async with Client(live.url) as client:
                t0 = time.perf_counter()
                first = tool_data(await client.call_tool("start_podcast", {"script": script_a}))
                first_elapsed = time.perf_counter() - t0
                assert first_elapsed < START_DEADLINE_SEC, (
                    f"first start_podcast blocked for {first_elapsed:.2f}s; "
                    f"expected < {START_DEADLINE_SEC}s"
                )
                t1 = time.perf_counter()
                second = tool_data(await client.call_tool("start_podcast", {"script": script_b}))
                second_elapsed = time.perf_counter() - t1
                assert second_elapsed < START_DEADLINE_SEC, (
                    f"second start_podcast blocked for {second_elapsed:.2f}s; "
                    f"expected < {START_DEADLINE_SEC}s"
                )
                assert first["job_id"] != second["job_id"]
                assert first["audio_uri"] != second["audio_uri"]
                uris.extend(
                    [
                        first["audio_uri"],
                        first["status_uri"],
                        second["audio_uri"],
                        second["status_uri"],
                    ]
                )

                live_tasks = [task for task in live.manager._tasks.values() if not task.done()]
                assert len(live_tasks) == 2, "MCP should schedule one asyncio.Task per job"

                saw_both_active = False
                second_active_before_first_done = False
                deadline = time.monotonic() + SHORT_POLL_TIMEOUT_SEC
                s1 = first
                s2 = second
                while time.monotonic() < deadline:
                    s1 = tool_data(
                        await client.call_tool("get_podcast_status", {"job_id": first["job_id"]})
                    )
                    s2 = tool_data(
                        await client.call_tool("get_podcast_status", {"job_id": second["job_id"]})
                    )
                    active = {JobStatus.queued, JobStatus.running}
                    if s1["status"] in active and s2["status"] in active:
                        saw_both_active = True
                    if s2["status"] in active and s1["status"] != JobStatus.succeeded:
                        second_active_before_first_done = True
                    if s1["status"] == JobStatus.succeeded and s2["status"] == JobStatus.succeeded:
                        break
                    await asyncio.sleep(0.15)
                else:
                    raise AssertionError(
                        f"concurrent jobs did not both succeed: {s1['status']}/{s2['status']} "
                        f"{s1.get('error')}/{s2.get('error')}"
                    )

                assert s1["status"] == JobStatus.succeeded
                assert s2["status"] == JobStatus.succeeded
                assert saw_both_active or second_active_before_first_done

            wav_a = tmp_path / f"mcp_conc_a_{suffix}.wav"
            wav_b = tmp_path / f"mcp_conc_b_{suffix}.wav"
            download_file(live.gcs_client, first["audio_uri"], wav_a)
            download_file(live.gcs_client, second["audio_uri"], wav_b)
            assert_mono_wav(wav_a)
            assert_mono_wav(wav_b)
            assert wav_a.read_bytes() != wav_b.read_bytes()
        finally:
            for uri in uris:
                delete_gcs_blob(live.gcs_client, uri)
