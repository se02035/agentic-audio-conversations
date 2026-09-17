"""Public ``tools`` barrel re-exports ingest/create entry points."""

from __future__ import annotations

import pytest

pytest.importorskip("google.adk")

from tts_audio_conversation.adk import tools  # noqa: E402
from tts_audio_conversation.adk.create_audio import (  # noqa: E402
    CREATE_AUDIO_TOOL_NAME,
    create_audio_conversation,
)
from tts_audio_conversation.adk.ingest import (  # noqa: E402
    ingest_uploaded_script,
    yaml_bytes_from_part,
)
from tts_audio_conversation.adk.mcp_client import configure_mcp_client, get_mcp_client  # noqa: E402


def test_tools_reexports_ingest_create_and_mcp_client() -> None:
    """``adk.tools`` stays a thin facade over the split tool modules."""
    assert tools.CREATE_AUDIO_TOOL_NAME == CREATE_AUDIO_TOOL_NAME
    assert tools.create_audio_conversation is create_audio_conversation
    assert tools.ingest_uploaded_script is ingest_uploaded_script
    assert tools.yaml_bytes_from_part is yaml_bytes_from_part
    assert tools.configure_mcp_client is configure_mcp_client
    assert tools.get_mcp_client is get_mcp_client
