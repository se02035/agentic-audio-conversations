"""Parse ADK API-server JSON events (``POST /run`` and ``GET session``)."""

from __future__ import annotations

import base64
from typing import Any


def event_list(payload: Any) -> list[Any]:
    """Normalize ``/run`` (list) or ``GET session`` (object with events)."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        events = payload.get("events")
        if isinstance(events, list):
            return events
    return []


def _parts(event: Any) -> list[Any]:
    """Return ``content.parts`` from one API-server event dict."""
    if not isinstance(event, dict):
        return []
    content = event.get("content")
    if not isinstance(content, dict):
        return []
    parts = content.get("parts")
    return parts if isinstance(parts, list) else []


def function_call_names(payload: Any) -> set[str]:
    """Collect ``functionCall.name`` values from API-server events."""
    names: set[str] = set()
    for event in event_list(payload):
        for part in _parts(event):
            if not isinstance(part, dict):
                continue
            call = part.get("functionCall") or part.get("function_call")
            if isinstance(call, dict) and call.get("name"):
                names.add(str(call["name"]))
    return names


def function_responses(payload: Any, name: str) -> list[dict[str, Any]]:
    """Collect ``functionResponse`` objects matching ``name``."""
    found: list[dict[str, Any]] = []
    for event in event_list(payload):
        for part in _parts(event):
            if not isinstance(part, dict):
                continue
            response = part.get("functionResponse") or part.get("function_response")
            if isinstance(response, dict) and response.get("name") == name:
                found.append(response)
    return found


def artifact_delta_filenames(payload: Any) -> set[str]:
    """Filenames from session ``actions.artifactDelta`` (ADK Web Artifacts tab)."""
    names: set[str] = set()
    for event in event_list(payload):
        if not isinstance(event, dict):
            continue
        actions = event.get("actions")
        if not isinstance(actions, dict):
            continue
        delta = actions.get("artifactDelta") or actions.get("artifact_delta")
        if isinstance(delta, dict):
            names.update(str(key) for key in delta)
    return names


def inline_file_part(filename: str, data: bytes, mime_type: str) -> dict[str, Any]:
    """Build a ``Content.parts[]`` inlineData object for ``POST /run``."""
    return {
        "inlineData": {
            "displayName": filename,
            "mimeType": mime_type,
            "data": base64.b64encode(data).decode("ascii"),
        }
    }
