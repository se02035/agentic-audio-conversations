"""ADK ``LlmAgent`` + ``App`` for MCP-backed audio overviews.

``adk web`` / ``adk api_server`` load this directory as a single-agent folder
(exports ``app`` and ``root_agent``). Requires the optional ``adk`` extra.
"""

from __future__ import annotations

from . import _syspath as _unshadow_mcp_sdk  # noqa: F401  # isort: skip

from dataclasses import dataclass
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.apps.app import App
from google.adk.models.google_llm import Gemini
from google.adk.plugins.save_files_as_artifacts_plugin import SaveFilesAsArtifactsPlugin
from google.adk.tools.long_running_tool import LongRunningFunctionTool
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.genai import types

from .config import (
    AgentSettings,
    apply_gemini_runtime_env,
    instruction_text,
    load_repo_dotenv,
)
from .create_audio import bind_create_audio_conversation
from .ingest import bind_ingest_uploaded_script
from .lro_plugin import ConversationJobClientPlugin
from .mcp_client import LLM_VISIBLE_MCP_TOOLS, McpConversationClient
from .mcp_toolset import RetryingMcpToolset

APP_NAME = "adk"
AGENT_NAME = "audio_overview"
AGENT_DESCRIPTION = (
    "Automated conversation and narration audio creator using Google Cloud "
    "Text-to-Speech Chirp 3 HD voices in the EU regional endpoint."
)


@dataclass(frozen=True)
class BuiltAgent:
    """Constructed ADK app plus the LRO client plugin (for tests)."""

    app: App
    root_agent: LlmAgent
    lro_plugin: ConversationJobClientPlugin
    settings: AgentSettings


def build_gemini(settings: AgentSettings) -> Gemini:
    """Gemini model with HTTP retry options from env."""
    apply_gemini_runtime_env()
    return Gemini(
        model=settings.adk_agent_model,
        retry_options=types.HttpRetryOptions(
            attempts=settings.adk_llm_retry_attempts,
            initial_delay=settings.adk_llm_retry_initial_delay,
            max_delay=settings.adk_llm_retry_max_delay,
        ),
    )


def build_app(
    settings: AgentSettings | None = None,
    *,
    download_bytes_fn: Any | None = None,
) -> BuiltAgent:
    """Wire ``LlmAgent``, MCP toolset, LRO plugin, and ``App``.

    Args:
        settings: Override env-backed settings (tests inject MCP URL / poll).
        download_bytes_fn: Optional ``gs://`` downloader for the resume plugin.

    Returns:
        Frozen ``BuiltAgent`` with ``app`` / ``root_agent`` for ``adk web`` /
        ``adk api_server``.
    """
    load_repo_dotenv()
    cfg = settings or AgentSettings()
    client = McpConversationClient(
        cfg.audio_conversation_mcp_url,
        download_bytes_fn=download_bytes_fn,
    )
    lro_plugin = ConversationJobClientPlugin(settings=cfg, client=client)
    mcp_toolset = RetryingMcpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=cfg.audio_conversation_mcp_url,
            timeout=30.0,
        ),
        tool_filter=list(LLM_VISIBLE_MCP_TOOLS),
    )
    agent = LlmAgent(
        name=AGENT_NAME,
        description=AGENT_DESCRIPTION,
        model=build_gemini(cfg),
        instruction=instruction_text(),
        tools=[
            bind_ingest_uploaded_script(client),
            LongRunningFunctionTool(bind_create_audio_conversation(client)),
            mcp_toolset,
        ],
    )
    app = App(
        name=APP_NAME,
        root_agent=agent,
        plugins=[SaveFilesAsArtifactsPlugin(), lro_plugin],
    )
    lro_plugin.bind_app(app)
    return BuiltAgent(app=app, root_agent=agent, lro_plugin=lro_plugin, settings=cfg)


load_repo_dotenv()
_built = build_app()
root_agent = _built.root_agent
app = _built.app
