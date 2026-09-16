"""Environment-backed settings for the ADK audio-overview adapter."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_MCP_URL = "http://127.0.0.1:8000/mcp"
DEFAULT_MODEL = "gemini-3.8-flash"


def repo_root() -> Path:
    """Return the repository root (directory containing ``pyproject.toml``)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path.cwd()


def load_repo_dotenv() -> None:
    """Load the repo-root ``.env`` (ADK only auto-loads an agent-directory ``.env``)."""
    env_path = repo_root() / ".env"
    if env_path.is_file():
        load_dotenv(env_path)
        return
    load_dotenv()


def apply_gemini_runtime_env() -> None:
    """Default Vertex/Gemini Enterprise + global location for the ADK process.

    Speech and translation stay on EU endpoints inside the MCP process. Do not
    point this process at an EU Gemini location.
    """
    os.environ.setdefault("GOOGLE_GENAI_USE_ENTERPRISE", "true")
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")


def example_script_path() -> Path:
    """Return the short playground YAML shipped next to this module."""
    return Path(__file__).with_name("example_script.yaml")


def instruction_text() -> str:
    """Load the agent instruction from ``instruction.md``."""
    path = Path(__file__).with_name("instruction.md")
    return path.read_text(encoding="utf-8")


class AgentSettings(BaseSettings):
    """ADK + MCP client knobs (Gemini global; MCP still talks to EU TTS)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    adk_agent_model: str = Field(default=DEFAULT_MODEL)
    adk_llm_retry_attempts: int = Field(default=5, ge=1)
    adk_llm_retry_initial_delay: float = Field(default=1.0, ge=0.0)
    adk_llm_retry_max_delay: float = Field(default=16.0, ge=0.0)
    audio_conversation_mcp_url: str = Field(default=DEFAULT_MCP_URL)
    audio_conversation_job_poll_interval_sec: float = Field(default=5.0, gt=0.0)
    audio_conversation_job_poll_timeout_sec: float = Field(default=1800.0, gt=0.0)
    google_cloud_project: str | None = Field(default=None)
