"""Boot the real ``adk api_server`` (no Gemini, dummy MCP URL)."""

from __future__ import annotations

from tests.integration.adk.api_server import ADK_APP_NAME, AdkApiServer


async def test_adk_api_server_lists_the_audio_overview_app(adk_api_server: AdkApiServer) -> None:
    """``GET /list-apps`` returns the single-agent folder name ``adk``."""
    apps = await adk_api_server.list_apps()
    assert ADK_APP_NAME in apps
    session = await adk_api_server.create_session("s-boot")
    assert session.get("id") == "s-boot"
