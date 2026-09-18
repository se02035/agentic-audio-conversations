"""Unit tests for the ADK A2A (Agent-to-Agent) adapter."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("google.adk")
pytest.importorskip("a2a")

from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette

from tests.unit.adk.helpers import delayed_synth, mocked_mcp_http
from tts_audio_conversation.adk.a2a import build_a2a_app, main
from tts_audio_conversation.adk.agent import AGENT_DESCRIPTION, AGENT_NAME, build_app
from tts_audio_conversation.adk.config import AgentSettings


def test_build_a2a_app_returns_starlette_app() -> None:
    """``build_a2a_app`` creates a Starlette application instance."""
    settings = AgentSettings(
        adk_a2a_host="127.0.0.1",
        adk_a2a_port=8001,
        adk_a2a_protocol="http",
    )
    app = build_a2a_app(settings)
    assert isinstance(app, Starlette)


async def test_a2a_agent_card_and_legacy_redirect() -> None:
    """The well-known endpoint serves the agent card and the legacy path redirects."""
    async with mocked_mcp_http(delayed_synth(0.0)) as live:
        settings = AgentSettings(
            audio_conversation_mcp_url=live.url,
            adk_a2a_host="127.0.0.1",
            adk_a2a_port=8001,
        )
        built = build_app(settings)
        app = build_a2a_app(settings, built_agent=built)

        try:
            async with app.router.lifespan_context(app):
                async with AsyncClient(
                    transport=ASGITransport(app=app),
                    base_url="http://127.0.0.1:8001",
                ) as client:
                    res = await client.get("/.well-known/agent-card.json")
                    assert res.status_code == 200
                    card = res.json()
                    assert card["name"] == AGENT_NAME
                    assert card["description"] == AGENT_DESCRIPTION

                    skill_names = {s["name"] for s in card.get("skills", [])}
                    assert "ingest_uploaded_script" in skill_names
                    assert "create_audio_conversation" in skill_names
                    assert "validate_script" in skill_names

                    # Test legacy v0.3 compatibility on /.well-known/agent.json
                    v0_3_res = await client.get("/.well-known/agent.json")
                    assert v0_3_res.status_code == 200
                    v0_3_card = v0_3_res.json()
                    assert v0_3_card["name"] == AGENT_NAME
                    assert "url" in v0_3_card
                    assert v0_3_card.get("protocolVersion") == "0.3.0"
                    assert "supportedInterfaces" not in v0_3_card
        finally:
            for tool in built.root_agent.tools:
                if hasattr(tool, "close"):
                    await tool.close()


async def test_a2a_custom_agent_card_override(tmp_path: Path) -> None:
    """Providing a custom card file overrides automatic card building."""
    card_data = {
        "name": "custom_audio_agent",
        "description": "Custom agent override description",
        "version": "1.2.3",
        "capabilities": {"streaming": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [],
        "supportedInterfaces": [
            {"url": "http://127.0.0.1:8001", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        ],
    }
    card_file = tmp_path / "custom_agent_card.json"
    card_file.write_text(json.dumps(card_data), encoding="utf-8")

    settings = AgentSettings(
        adk_a2a_agent_card_path=str(card_file),
        adk_a2a_port=8001,
    )
    app = build_a2a_app(settings)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8001",
        ) as client:
            res = await client.get("/.well-known/agent-card.json")
            assert res.status_code == 200
            card = res.json()
            assert card["name"] == "custom_audio_agent"
            assert card["description"] == "Custom agent override description"


def test_main_cli_runs_uvicorn_with_args() -> None:
    """CLI parses arguments and calls uvicorn.run."""
    with patch("uvicorn.run") as mock_run:
        main(["--host", "0.0.0.0", "--port", "8005"])
        assert mock_run.call_count == 1
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["host"] == "0.0.0.0"
        assert call_kwargs["port"] == 8005


def test_main_cli_with_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI with --reload passes import string to uvicorn.run."""
    monkeypatch.delenv("ADK_A2A_AGENT_ENDPOINT", raising=False)
    monkeypatch.delenv("TUNNEL_ADDRESS", raising=False)
    with patch("uvicorn.run") as mock_run:
        main(["--reload", "--port", "8006", "--endpoint", "https://reload-tunnel.example.com"])
        assert mock_run.call_count == 1
        args, kwargs = mock_run.call_args
        assert args[0] == "tts_audio_conversation.adk.a2a:a2a_app"
        assert kwargs["reload"] is True
        assert kwargs["port"] == 8006


async def test_a2a_agent_endpoint_config_updates_card_url() -> None:
    """Configuring adk_a2a_agent_endpoint updates the URL in the agent card."""
    settings = AgentSettings(
        adk_a2a_host="127.0.0.1",
        adk_a2a_port=8001,
        adk_a2a_agent_endpoint="https://audio-agent.tunnel.example.com",
    )
    app = build_a2a_app(settings)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:8001",
        ) as client:
            res = await client.get("/.well-known/agent-card.json")
            assert res.status_code == 200
            card = res.json()
            assert card["supportedInterfaces"][0]["url"] == "https://audio-agent.tunnel.example.com"


async def test_a2a_with_programmatic_agent_card_builder() -> None:
    """Pre-building card via AgentCardBuilder and passing agent_card works."""
    from google.adk.agents import LlmAgent

    from tts_audio_conversation.adk.a2a import AgentCardBuilder

    dummy_agent = LlmAgent(name="direct_builder_agent")
    builder = AgentCardBuilder(
        agent=dummy_agent,
        rpc_url="https://direct.tunnel.example.com",
    )
    card = await builder.build()

    built = build_app(AgentSettings())
    app = build_a2a_app(agent_card=card, built_agent=built)

    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://127.0.0.1:8001",
            ) as client:
                res = await client.get("/.well-known/agent-card.json")
                assert res.status_code == 200
                data = res.json()
                assert data["name"] == "direct_builder_agent"
                assert data["supportedInterfaces"][0]["url"] == "https://direct.tunnel.example.com"
    finally:
        for tool in built.root_agent.tools:
            if hasattr(tool, "close"):
                await tool.close()
