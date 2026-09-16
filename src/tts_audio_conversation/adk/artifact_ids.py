"""Collision-free ADK artifact filenames (no ``google.adk`` import)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

PENDING_LRO_STATE_KEY = "pending_audio_lro"
OVERVIEWS_STATE_KEY = "audio_overviews"
LATEST_SCRIPT_URI_KEY = "latest_script_uri"
LATEST_SCRIPT_ID_KEY = "latest_script_id"
LATEST_SCRIPT_ARTIFACT_KEY = "latest_script_artifact"
LATEST_LANGUAGE_CODE_KEY = "latest_language_code"

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_CANONICAL_SCRIPT = re.compile(r"^script_[A-Za-z0-9._-]+\.ya?ml$", re.IGNORECASE)
_YAML_SUFFIXES = (".yaml", ".yml")


def sanitize_artifact_token(value: str, *, default: str = "unknown") -> str:
    """Return a filename-safe token without introducing a ``user:`` prefix.

    Args:
        value: Raw identifier (script id, job id, or BCP-47 language).
        default: Replacement when the sanitized token is empty.

    Returns:
        Token using only ``[A-Za-z0-9._-]``, at most 128 characters.
    """
    text = value.strip()
    if text.lower().startswith("user:"):
        text = text[5:]
    text = _UNSAFE.sub("-", text).strip(".-")
    return (text or default)[:128]


def script_artifact_name(script_id: str) -> str:
    """Return ``script_{script_id}.yaml`` for an ingested MCP script."""
    return f"script_{sanitize_artifact_token(script_id)}.yaml"


def audio_artifact_name(job_id: str, language_code: str) -> str:
    """Return ``audio_{job_id}_{language}.wav`` for a synthesis job."""
    lang = sanitize_artifact_token(language_code, default="und")
    return f"audio_{sanitize_artifact_token(job_id)}_{lang}.wav"


def pick_uploaded_yaml_name(names: Sequence[str]) -> str | None:
    """Choose a chat-uploaded YAML over a canonical ``script_{id}.yaml``.

    Args:
        names: Session artifact filenames.

    Returns:
        The last non-canonical YAML name, else the last YAML name, else ``None``.
    """
    yaml_names = [name for name in names if name.lower().endswith(_YAML_SUFFIXES)]
    if not yaml_names:
        return None
    uploads = [name for name in yaml_names if not _CANONICAL_SCRIPT.match(name)]
    if uploads:
        return uploads[-1]
    return yaml_names[-1]


def upsert_audio_overview(
    index: Sequence[Mapping[str, Any]],
    entry: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Replace or append an overview row keyed by ``job_id`` when present."""
    job_id = entry.get("job_id")
    rows = [dict(row) for row in index]
    if job_id:
        rows = [row for row in rows if row.get("job_id") != job_id]
    rows.append(dict(entry))
    return rows
