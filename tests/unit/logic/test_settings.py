"""Tests for environment-backed Settings."""

from __future__ import annotations

import pytest

from tts_audio_conversation.logic.settings import Settings


def test_require_loopback_mcp_host_accepts_local_binds() -> None:
    """Loopback IPv4, IPv6, and localhost remain valid MCP binds."""
    assert Settings(mcp_host="127.0.0.1").require_loopback_mcp_host() == "127.0.0.1"
    assert Settings(mcp_host="localhost").require_loopback_mcp_host() == "localhost"
    assert Settings(mcp_host="::1").require_loopback_mcp_host() == "::1"
    assert Settings(mcp_host="[::1]").require_loopback_mcp_host() == "[::1]"


def test_require_loopback_mcp_host_rejects_non_loopback() -> None:
    """Non-loopback binds are rejected with a configuration error."""
    with pytest.raises(ValueError, match="loopback"):
        Settings(mcp_host="0.0.0.0").require_loopback_mcp_host()
    with pytest.raises(ValueError, match="loopback"):
        Settings(mcp_host="192.168.1.10").require_loopback_mcp_host()
    with pytest.raises(ValueError, match="loopback"):
        Settings(mcp_host="::").require_loopback_mcp_host()
