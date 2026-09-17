"""Tests for ADK Web sys.path unshadowing of the MCP SDK."""

from __future__ import annotations

import sys
from pathlib import Path

from tts_audio_conversation.adk._syspath import unshadow_mcp_sdk


def test_unshadow_mcp_sdk_removes_adk_parent() -> None:
    """The folder that contains this repo's ``mcp/`` adapter is dropped from sys.path."""
    parent = str(Path(unshadow_mcp_sdk.__code__.co_filename).resolve().parent.parent)
    original = list(sys.path)
    sys.path.insert(0, parent)
    try:
        unshadow_mcp_sdk()
        assert parent not in sys.path
    finally:
        sys.path[:] = original
