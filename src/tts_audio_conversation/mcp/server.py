"""Anonymous FastMCP Streamable HTTP server for conversation jobs."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from opentelemetry import trace
from pydantic import Field

from tts_audio_conversation.logic.exceptions import (
    JobNotFound,
    ScriptPayloadError,
    VoiceCatalogError,
)
from tts_audio_conversation.logic.jobs.models import JobRecord
from tts_audio_conversation.logic.results import (
    ScriptUploadResult,
    ScriptValidationResult,
    TranslateScriptResult,
)
from tts_audio_conversation.logic.service import (
    AudioConversationService,
    create_audio_conversation_service_from_adc,
)
from tts_audio_conversation.logic.settings import Settings
from tts_audio_conversation.logic.telemetry import setup_telemetry

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

MCP_PATH = "/mcp"


def create_server(
    service: AudioConversationService,
    *,
    name: str = "tts-audio-conversation",
) -> FastMCP[Any]:
    """Build a FastMCP app bound to an ``AudioConversationService``.

    Args:
        service: Library facade used by all tools.
        name: MCP server name shown to clients.

    Returns:
        Configured FastMCP instance (no auth).
    """
    mcp = FastMCP(name, tasks=False)

    @mcp.tool(description="Upload an inline YAML/JSON script to staging GCS; return script_uri.")
    def upload_script(
        script: str = Field(description="YAML or JSON conversation script payload."),
    ) -> ScriptUploadResult:
        """Write ``…/scripts/{id}/script.yaml`` and return its URI."""
        try:
            return service.upload_script(script)
        except ScriptPayloadError as exc:
            raise ToolError(str(exc)) from exc
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(description="Validate a script at a gs:// URI. Does not call list_voices.")
    def validate_script(
        script_uri: str = Field(description="gs:// URI from upload_script."),
    ) -> ScriptValidationResult:
        """Soft-validate an uploaded script."""
        return service.validate_script(script_uri)

    @mcp.tool(
        description=(
            "Translate a gs:// script via translate-eu; write sibling script.{lang}.yaml; "
            "return output_uri (not body)."
        )
    )
    def translate_script(
        script_uri: str = Field(description="gs:// URI from upload_script."),
        target_language: str = Field(description="BCP-47 target, e.g. de-DE."),
    ) -> TranslateScriptResult:
        """Translate and return the output URI for start_conversation."""
        try:
            return service.translate_script(script_uri, target_language)
        except ScriptPayloadError as exc:
            raise ToolError(str(exc)) from exc
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        description=(
            "Start synthesis from a gs:// script_uri. Preflight EU list_voices, write queued "
            "status.json, return immediately. Poll get_conversation_status."
        )
    )
    async def start_conversation(
        script_uri: str = Field(description="gs:// URI of the script to synthesize."),
    ) -> JobRecord:
        """Enqueue a job and return before any TTS call."""
        with tracer.start_as_current_span("start_conversation"):
            try:
                return await service.create_audio(script_uri)
            except ScriptPayloadError as exc:
                raise ToolError(str(exc)) from exc
            except VoiceCatalogError as exc:
                raise ToolError(str(exc)) from exc
            except ValueError as exc:
                raise ToolError(str(exc)) from exc

    @mcp.tool(description="Poll a conversation job. Uses in-memory state, else GCS status.json.")
    async def get_conversation_status(
        job_id: str = Field(description="Job id returned by start_conversation."),
    ) -> JobRecord:
        """Return queued, running, succeeded, failed, or cancelled."""
        try:
            return await service.get_job(job_id)
        except JobNotFound as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        description=(
            "Cancel a queued or running job. Returns immediately. The current Cloud TTS "
            "batch may finish; later batches are skipped and audio is not uploaded."
        )
    )
    async def cancel_conversation(
        job_id: str = Field(description="Job id returned by start_conversation."),
    ) -> JobRecord:
        """Set the cooperative cancel flag without waiting for TTS."""
        try:
            return await service.cancel_job(job_id)
        except JobNotFound as exc:
            raise ToolError(str(exc)) from exc

    return mcp


def main() -> None:
    """Load ``.env``, configure tracing, and serve Streamable HTTP (no auth)."""
    load_dotenv()
    os.environ.setdefault("FASTMCP_TELEMETRY_MODE", "native")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    settings = Settings()
    try:
        host = settings.require_loopback_mcp_host()
        settings.require_gcs_bucket()
    except ValueError as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc
    setup_telemetry(settings)
    service = create_audio_conversation_service_from_adc(settings)
    mcp = create_server(service)
    url = f"http://{host}:{settings.mcp_port}{MCP_PATH}"
    logger.info("MCP Streamable HTTP listening at %s (anonymous, single process)", url)
    mcp.run(
        transport="http",
        host=host,
        port=settings.mcp_port,
        path=MCP_PATH,
    )
