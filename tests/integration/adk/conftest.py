"""Fixtures that start the real ``adk api_server`` process for integration tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.integration.adk.api_server import AdkApiServer, run_adk_api_server
from tests.integration.adk.stack import AdkAgentStack
from tests.unit.adk.helpers import delayed_synth, mocked_mcp_http

DUMMY_MCP_URL = "http://127.0.0.1:9/mcp"


@pytest.fixture
def adk_gcs_stub_dir(tmp_path: Path) -> Path:
    """Directory the API-server child reads for mirrored FakeGcs WAV blobs."""
    path = tmp_path / "adk-gcs-stub"
    path.mkdir()
    return path


@pytest.fixture
def adk_mcp_url() -> str:
    """MCP Streamable HTTP URL when the test does not need a live mock."""
    return DUMMY_MCP_URL


@pytest.fixture
async def adk_api_server(
    adk_mcp_url: str,
    adk_gcs_stub_dir: Path,
) -> AsyncIterator[AdkApiServer]:
    """Start ``adk api_server`` before the test and terminate it after.

    Function-scoped: isolated process, in-memory sessions/artifacts, and teardown
    even when the test fails. Default MCP URL is a dummy (boot tests). Gemini LRO
    tests use ``adk_agent_stack`` instead, which starts mocked MCP first.
    """
    pytest.importorskip("google.adk")
    pytest.importorskip("httpx")
    async with run_adk_api_server(
        mcp_url=adk_mcp_url,
        gcs_stub_dir=adk_gcs_stub_dir,
    ) as api:
        yield api


@pytest.fixture
def adk_synth_delay_sec() -> float:
    """Fake TTS delay so the test can assert LRO returns before synthesis ends."""
    return 3.0


@pytest.fixture
async def adk_agent_stack(
    adk_gcs_stub_dir: Path,
    adk_synth_delay_sec: float,
) -> AsyncIterator[AdkAgentStack]:
    """Mocked MCP HTTP, then the real ``adk api_server`` pointed at that MCP.

    Teardown stops the API server first, then MCP. Live MCP (no TTS stub) belongs
    in a later e2e-style suite, not this fixture.
    """
    pytest.importorskip("google.adk")
    pytest.importorskip("httpx")
    async with mocked_mcp_http(delayed_synth(adk_synth_delay_sec)) as live:
        live.gcs.persist_dir = adk_gcs_stub_dir
        async with run_adk_api_server(
            mcp_url=live.url,
            gcs_stub_dir=adk_gcs_stub_dir,
        ) as api:
            yield AdkAgentStack(
                api=api,
                mcp=live,
                synth_delay_sec=adk_synth_delay_sec,
            )
