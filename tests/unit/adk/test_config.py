"""Unit tests for ADK adapter settings helpers."""

from __future__ import annotations

import os

import pytest

from tts_audio_conversation.adk.config import (
    apply_gemini_runtime_env,
    example_script_path,
    instruction_text,
    repo_root,
)


def test_instruction_text_loads_without_session_state_placeholders() -> None:
    """Instruction must not use ``{var}`` — ADK interpolates those from session state."""
    text = instruction_text()
    assert "ingest_uploaded_script" in text
    assert "create_audio_conversation" in text
    assert "{script_id}" not in text
    assert "{script_uri}" not in text


def test_example_script_and_repo_root_exist() -> None:
    """Playground YAML and ``pyproject.toml`` resolve from the adapter package."""
    assert example_script_path().is_file()
    assert (repo_root() / "pyproject.toml").is_file()


def test_apply_gemini_runtime_env_defaults_global(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gemini stays on the global location; speech residency is the MCP process."""
    monkeypatch.delenv("GOOGLE_GENAI_USE_ENTERPRISE", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    apply_gemini_runtime_env()
    assert os.environ["GOOGLE_CLOUD_LOCATION"] == "global"
    assert os.environ["GOOGLE_GENAI_USE_ENTERPRISE"] == "true"
    assert os.environ["GOOGLE_GENAI_USE_VERTEXAI"] == "true"
