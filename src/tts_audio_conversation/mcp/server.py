"""Anonymous FastMCP Streamable HTTP server for conversation jobs."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
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


def instruction_text() -> str:
    """Load MCP client instructions from ``instruction.md``."""
    return Path(__file__).with_name("instruction.md").read_text(encoding="utf-8")


def _annotations(
    *,
    read_only: bool,
    destructive: bool,
    idempotent: bool,
) -> ToolAnnotations:
    """Build MCP tool hints (Gemini Enterprise uses readOnlyHint for consent)."""
    return ToolAnnotations(
        read_only_hint=read_only,
        destructive_hint=destructive,
        idempotent_hint=idempotent,
        open_world_hint=True,
    )


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
    mcp = FastMCP(name, instructions=instruction_text(), tasks=False)

    @mcp.tool(
        description=(
            "Store an inline YAML/JSON conversation script in staging GCS. "
            "Call this first, then validate_script with the returned script_uri."
        ),
        annotations=_annotations(read_only=False, destructive=False, idempotent=False),
    )
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

    @mcp.tool(
        description=(
            "Check that a gs:// script is valid before synthesis. "
            "If valid is false, explain error and stop; do not call start_conversation."
        ),
        annotations=_annotations(read_only=True, destructive=False, idempotent=True),
    )
    def validate_script(
        script_uri: str = Field(description="gs:// URI from upload_script."),
    ) -> ScriptValidationResult:
        """Soft-validate an uploaded script."""
        return service.validate_script(script_uri)

    @mcp.tool(
        description=(
            "Translate a gs:// script to another language only when the user asked. "
            "Pass the returned output_uri to start_conversation."
        ),
        annotations=_annotations(read_only=False, destructive=False, idempotent=True),
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
            "Start billed TTS from a gs:// script_uri. Returns immediately with job_id "
            "and status queued. Poll get_conversation_status; the WAV is not ready yet."
        ),
        annotations=_annotations(read_only=False, destructive=True, idempotent=False),
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

    @mcp.tool(
        description=(
            "Poll a job started by start_conversation. Repeat with a delay until "
            "succeeded, failed, or cancelled. On success, report audio_uri."
        ),
        annotations=_annotations(read_only=True, destructive=False, idempotent=True),
    )
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
            "Stop a queued or running job when the user asks to cancel. "
            "The current TTS batch may finish; later batches are skipped."
        ),
        annotations=_annotations(read_only=False, destructive=True, idempotent=True),
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
