"""ADK adapter ↔ MCP HTTP wiring tests (mocked TTS/GCS, no Gemini, no api_server)."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

from tests.unit.adk.helpers import mocked_mcp_http
from tests.unit.mcp.helpers import write_fake_wav
from tts_audio_conversation.adk.mcp_client import McpConversationClient
from tts_audio_conversation.logic.jobs.models import JobStatus


async def test_start_returns_queued_without_waiting(
    sample_script_yaml: str,
) -> None:
    """``start_conversation`` via the adapter client returns before TTS finishes."""
    started = threading.Event()
    release = threading.Event()

    def blocked_synth(_client: Any, _script: Any, output_path: Path, **_kwargs: Any) -> Path:
        started.set()
        assert release.wait(timeout=8)
        return write_fake_wav(output_path)

    async with mocked_mcp_http(blocked_synth) as live:
        client = McpConversationClient(live.url, connect_attempts=20, connect_retry_delay_sec=0.05)
        uploaded = await client.upload_script(sample_script_yaml)
        t0 = time.perf_counter()
        job = await client.start_conversation(uploaded["script_uri"])
        try:
            elapsed = time.perf_counter() - t0
            assert elapsed < 1.0
            assert job["status"] in {JobStatus.queued.value, JobStatus.running.value}
            assert job["job_id"]
            assert await asyncio.to_thread(started.wait, 3)
            status = await client.get_conversation_status(job["job_id"])
            assert status["status"] in {JobStatus.queued.value, JobStatus.running.value}
        finally:
            release.set()
