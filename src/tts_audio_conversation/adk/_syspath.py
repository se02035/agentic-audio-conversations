"""Keep ADK from shadowing the MCP Python SDK with this repo's ``mcp/`` adapter."""

from __future__ import annotations

import sys
from pathlib import Path


def unshadow_mcp_sdk() -> None:
    """Remove ``src/tts_audio_conversation`` from ``sys.path`` if ADK prepended it.

    ``adk web`` / ``adk api_server …/tts_audio_conversation/adk`` add the parent
    folder to ``sys.path``. That parent contains our ``mcp`` adapter package,
    which would otherwise win over the Model Context Protocol SDK import ``mcp``
    used by ``google.adk``.
    """
    parent = str(Path(__file__).resolve().parent.parent)
    while parent in sys.path:
        sys.path.remove(parent)


unshadow_mcp_sdk()
