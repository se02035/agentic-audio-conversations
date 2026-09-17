"""ADK long-running function tool: start MCP synthesis and return ``queued``."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from google.adk.tools.tool_context import ToolContext

from .artifact_ids import (
    LATEST_LANGUAGE_CODE_KEY,
    LATEST_SCRIPT_ARTIFACT_KEY,
    LATEST_SCRIPT_ID_KEY,
    OVERVIEWS_STATE_KEY,
    PENDING_LRO_STATE_KEY,
    upsert_audio_overview,
)
from .mcp_client import McpConversationClient, get_mcp_client

CREATE_AUDIO_TOOL_NAME = "create_audio_conversation"


async def create_audio_conversation(script_uri: str, tool_context: ToolContext) -> dict[str, Any]:
    """Start MCP synthesis and return ``queued`` immediately (no poll or download).

    Args:
        script_uri: ``gs://`` URI from ingest/validate.
        tool_context: ADK tool context (function_call id + session state).

    Returns:
        ``job_id``, ``status``, ``script_uri``, and ``audio_uri``.
    """
    return await _create_audio_conversation(script_uri, tool_context, get_mcp_client())


def bind_create_audio_conversation(
    client: McpConversationClient,
) -> Callable[[str, ToolContext], Awaitable[dict[str, Any]]]:
    """Return a create tool that always uses ``client`` (not a process-wide override)."""

    @wraps(create_audio_conversation)
    async def bound(script_uri: str, tool_context: ToolContext) -> dict[str, Any]:
        return await _create_audio_conversation(script_uri, tool_context, client)

    return bound


async def _create_audio_conversation(
    script_uri: str,
    tool_context: ToolContext,
    client: McpConversationClient,
) -> dict[str, Any]:
    """Start synthesis through ``client`` and stash a pending LRO snapshot."""
    started = await client.start_conversation(script_uri)
    job_id = started["job_id"]
    language_code = str(tool_context.state.get(LATEST_LANGUAGE_CODE_KEY) or "und")
    script_artifact = str(tool_context.state.get(LATEST_SCRIPT_ARTIFACT_KEY) or "")
    script_id = str(tool_context.state.get(LATEST_SCRIPT_ID_KEY) or "")
    pending = {
        "job_id": job_id,
        "status": started["status"],
        "script_id": script_id,
        "script_uri": started["script_uri"],
        "audio_uri": started.get("audio_uri") or "",
        "language_code": language_code,
        "script_artifact": script_artifact,
        "function_call_id": tool_context.function_call_id,
        "tool_name": CREATE_AUDIO_TOOL_NAME,
    }
    tool_context.state[PENDING_LRO_STATE_KEY] = pending
    overviews = list(tool_context.state.get(OVERVIEWS_STATE_KEY) or [])
    tool_context.state[OVERVIEWS_STATE_KEY] = upsert_audio_overview(
        overviews,
        {
            "script_id": script_id,
            "script_uri": started["script_uri"],
            "script_artifact": script_artifact,
            "job_id": job_id,
            "language_code": language_code,
            "status": started["status"],
        },
    )
    return {
        "job_id": job_id,
        "status": started["status"],
        "script_uri": started["script_uri"],
        "audio_uri": started.get("audio_uri") or "",
    }
