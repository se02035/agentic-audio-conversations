"""Shared types for ADK integration stacks (real api_server + optional mocked MCP)."""

from __future__ import annotations

from dataclasses import dataclass

from tests.integration.adk.api_server import AdkApiServer
from tests.unit.adk.helpers import MockedMcpHttp


@dataclass(frozen=True)
class AdkAgentStack:
    """Real ``adk api_server`` plus in-process mocked MCP HTTP (fake TTS/GCS)."""

    api: AdkApiServer
    mcp: MockedMcpHttp
    synth_delay_sec: float
