"""ADK function tool: ingest a chat YAML artifact via MCP upload."""

from __future__ import annotations

from typing import Any

from google.adk.tools.tool_context import ToolContext
from google.genai import types

from .artifact_ids import (
    LATEST_LANGUAGE_CODE_KEY,
    LATEST_SCRIPT_ARTIFACT_KEY,
    LATEST_SCRIPT_ID_KEY,
    LATEST_SCRIPT_URI_KEY,
    OVERVIEWS_STATE_KEY,
    pick_uploaded_yaml_name,
    script_artifact_name,
    upsert_audio_overview,
)
from .mcp_client import get_mcp_client, language_code_from_script


def yaml_bytes_from_part(part: types.Part) -> bytes:
    """Return YAML bytes from an artifact part (inline blob or text)."""
    inline = part.inline_data
    if inline is not None and inline.data:
        return bytes(inline.data)
    if part.text:
        return part.text.encode("utf-8")
    raise ValueError("Uploaded artifact has no YAML bytes or text.")


async def ingest_uploaded_script(tool_context: ToolContext) -> dict[str, Any]:
    """Load the latest chat YAML, MCP-upload it, and save ``script_{id}.yaml``.

    Returns:
        ``status``, ``script_uri``, ``script_id``, and ``script_artifact``.
    """
    names = await tool_context.list_artifacts()
    filename = pick_uploaded_yaml_name(names)
    if filename is None:
        return {
            "status": "error",
            "error": (
                "No YAML artifact found. Ask the user to upload a local "
                "script.yaml (or .yml) in this chat."
            ),
        }
    part = await tool_context.load_artifact(filename)
    if part is None:
        return {"status": "error", "error": f"Could not load artifact '{filename}'."}
    try:
        yaml_bytes = yaml_bytes_from_part(part)
        text = yaml_bytes.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        return {"status": "error", "error": str(exc)}
    client = get_mcp_client()
    uploaded = await client.upload_script(text)
    script_id = str(uploaded.get("script_id") or "")
    script_uri = str(uploaded.get("script_uri") or "")
    if not script_id or not script_uri:
        return {"status": "error", "error": "upload_script did not return script_id/script_uri."}
    artifact_name = script_artifact_name(script_id)
    language_code = language_code_from_script(text)
    await tool_context.save_artifact(
        artifact_name,
        types.Part(
            inline_data=types.Blob(
                mime_type="application/yaml",
                data=yaml_bytes,
                display_name=artifact_name,
            )
        ),
    )
    tool_context.state[LATEST_SCRIPT_URI_KEY] = script_uri
    tool_context.state[LATEST_SCRIPT_ID_KEY] = script_id
    tool_context.state[LATEST_SCRIPT_ARTIFACT_KEY] = artifact_name
    tool_context.state[LATEST_LANGUAGE_CODE_KEY] = language_code
    overviews = list(tool_context.state.get(OVERVIEWS_STATE_KEY) or [])
    tool_context.state[OVERVIEWS_STATE_KEY] = upsert_audio_overview(
        overviews,
        {
            "script_id": script_id,
            "script_uri": script_uri,
            "script_artifact": artifact_name,
            "language_code": language_code,
            "status": "ingested",
        },
    )
    return {
        "status": "ok",
        "script_uri": script_uri,
        "script_id": script_id,
        "script_artifact": artifact_name,
        "language_code": language_code,
    }
