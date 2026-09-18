"""Integration tests for the A2A server running over a live TCP socket."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn

pytest.importorskip("google.adk")
pytest.importorskip("a2a")

from tests.unit.adk.helpers import delayed_synth, free_port, mocked_mcp_http
from tts_audio_conversation.adk.a2a import build_a2a_app
from tts_audio_conversation.adk.agent import AGENT_DESCRIPTION, AGENT_NAME
from tts_audio_conversation.adk.config import AgentSettings


@asynccontextmanager
async def run_live_a2a_server(settings: AgentSettings) -> AsyncIterator[str]:
    """Start an in-process Uvicorn server hosting the A2A Starlette app on a free port."""
    port = settings.adk_a2a_port
    app = build_a2a_app(settings)
    config = uvicorn.Config(
        app,
        host=settings.adk_a2a_host,
        port=port,
        log_level="warning",
        lifespan="on",
        timeout_graceful_shutdown=2,
    )
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve())
    try:
        deadline = time.monotonic() + 10.0
        while not server.started:
            if serve_task.done():
                raise AssertionError(f"A2A server failed to start: {serve_task.exception()!r}")
            if time.monotonic() > deadline:
                raise AssertionError("A2A server did not start within 10s")
            await asyncio.sleep(0.05)
        yield f"http://{settings.adk_a2a_host}:{port}"
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(serve_task, timeout=5)
        except TimeoutError:
            serve_task.cancel()


async def test_a2a_server_serves_agent_card_and_redirect_over_tcp() -> None:
    """Live A2A server serves agent card over HTTP and redirects legacy path."""
    async with mocked_mcp_http(delayed_synth(0.0)) as live_mcp:
        port = free_port()
        settings = AgentSettings(
            audio_conversation_mcp_url=live_mcp.url,
            adk_a2a_host="127.0.0.1",
            adk_a2a_port=port,
            adk_a2a_agent_endpoint="",
            adk_a2a_tunnel_address="",
        )
        async with run_live_a2a_server(settings) as base_url:
            async with httpx.AsyncClient(base_url=base_url) as client:
                res = await client.get("/.well-known/agent-card.json")
                assert res.status_code == 200
                card = res.json()
                assert card["name"] == AGENT_NAME
                assert card["description"] == AGENT_DESCRIPTION

                interfaces = card.get("supportedInterfaces", [])
                assert any(f":{port}" in iface.get("url", "") for iface in interfaces)

                # Test legacy v0.3 agent.json serving over live HTTP socket
                v0_3_res = await client.get("/.well-known/agent.json")
                assert v0_3_res.status_code == 200
                v0_3_card = v0_3_res.json()
                assert v0_3_card["name"] == AGENT_NAME
                assert f":{port}" in v0_3_card.get("url", "")
                assert v0_3_card.get("protocolVersion") == "0.3.0"
                assert "supportedInterfaces" not in v0_3_card


async def test_a2a_server_handles_jsonrpc_list_tasks() -> None:
    """Live A2A server dispatches JSON-RPC requests via HTTP POST."""
    async with mocked_mcp_http(delayed_synth(0.0)) as live_mcp:
        port = free_port()
        settings = AgentSettings(
            audio_conversation_mcp_url=live_mcp.url,
            adk_a2a_host="127.0.0.1",
            adk_a2a_port=port,
        )
        async with run_live_a2a_server(settings) as base_url:
            async with httpx.AsyncClient(base_url=base_url) as client:
                payload = {
                    "jsonrpc": "2.0",
                    "id": "req-1",
                    "method": "ListTasks",
                    "params": {},
                }
                res = await client.post("/", json=payload, headers={"A2A-Version": "1.0"})
                assert res.status_code == 200
                data = res.json()
                assert data["id"] == "req-1"
                assert data["jsonrpc"] == "2.0"
                assert "result" in data
                assert data["result"]["tasks"] == []


async def test_a2a_server_serves_custom_endpoint_over_tcp() -> None:
    """Live A2A server advertises custom tunnel/endpoint URL over HTTP."""
    async with mocked_mcp_http(delayed_synth(0.0)) as live_mcp:
        port = free_port()
        custom_endpoint = "https://audio-live-tunnel.ngrok-free.app"
        settings = AgentSettings(
            audio_conversation_mcp_url=live_mcp.url,
            adk_a2a_host="127.0.0.1",
            adk_a2a_port=port,
            adk_a2a_agent_endpoint=custom_endpoint,
        )
        async with run_live_a2a_server(settings) as base_url:
            async with httpx.AsyncClient(base_url=base_url) as client:
                res = await client.get("/.well-known/agent-card.json")
                assert res.status_code == 200
                card = res.json()
                interfaces = card.get("supportedInterfaces", [])
                assert len(interfaces) > 0
                assert interfaces[0]["url"] == custom_endpoint
