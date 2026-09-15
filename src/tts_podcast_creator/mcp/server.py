"""Anonymous FastMCP Streamable HTTP server for podcast jobs."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from opentelemetry import trace
from pydantic import BaseModel, Field

from tts_podcast_creator.logic.exceptions import GcsResidencyError, JobNotFound, ScriptPayloadError
from tts_podcast_creator.logic.settings import Settings
from tts_podcast_creator.logic.storage import require_eu_bucket
from tts_podcast_creator.logic.telemetry import setup_telemetry
from tts_podcast_creator.mcp.jobs import JobManager, JobRecord, validate_payload

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

MCP_PATH = "/mcp"


class ValidateScriptResult(BaseModel):
    """Structured result for ``validate_script``."""

    valid: bool
    title: str = ""
    language_code: str = ""
    speakers: list[str] = Field(default_factory=list)
    turn_count: int = 0
    total_characters: int = 0
    error: str | None = None


def create_server(manager: JobManager, *, name: str = "tts-podcast-creator") -> FastMCP[Any]:
    """Build a FastMCP app bound to an existing ``JobManager``.

    Args:
        manager: Job manager used by the tools.
        name: MCP server name shown to clients.

    Returns:
        Configured FastMCP instance (no auth).
    """
    mcp = FastMCP(name, tasks=False)

    @mcp.tool(
        description=(
            "Start single- or multi-speaker podcast synthesis. Validates voices via "
            "EU list_voices, writes queued status.json, and returns immediately. "
            "Poll get_podcast_status; audio is never downloaded here."
        )
    )
    async def start_podcast(
        script: str = Field(description="YAML or JSON podcast script payload."),
        translate_to: str | None = Field(
            default=None,
            description="Optional BCP-47 language tag. Translation runs in the worker, not here.",
        ),
    ) -> JobRecord:
        """Validate the script, enqueue a job, and return before any TTS call."""
        with tracer.start_as_current_span("start_podcast"):
            try:
                return await manager.start(script, translate_to)
            except ScriptPayloadError as exc:
                raise ToolError(str(exc)) from exc
            except ValueError as exc:
                raise ToolError(str(exc)) from exc

    @mcp.tool(description="Poll a podcast job. Uses in-memory state, else GCS status.json.")
    async def get_podcast_status(
        job_id: str = Field(description="Job id returned by start_podcast."),
    ) -> JobRecord:
        """Return queued, running, succeeded, failed, or cancelled."""
        try:
            return await manager.get_status(job_id)
        except JobNotFound as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        description=(
            "Cancel a queued or running job. Returns immediately. The current Cloud TTS "
            "batch may finish; later batches are skipped and audio is not uploaded."
        )
    )
    async def cancel_podcast(
        job_id: str = Field(description="Job id returned by start_podcast."),
    ) -> JobRecord:
        """Set the cooperative cancel flag without waiting for TTS."""
        try:
            return await manager.cancel(job_id)
        except JobNotFound as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(description="Validate a YAML or JSON script payload. Does not write GCS.")
    def validate_script(
        script: str = Field(description="YAML or JSON podcast script payload."),
    ) -> ValidateScriptResult:
        """Check schema and PODCAST_MAX_SCRIPT_BYTES without starting a job."""
        return ValidateScriptResult.model_validate(validate_payload(script, manager.settings))

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
        bucket = settings.require_gcs_bucket()
        from google.cloud import storage  # type: ignore[attr-defined]

        from tts_podcast_creator.logic.auth import get_credentials_and_project

        credentials, project_id = get_credentials_and_project()
        gcs_client = storage.Client(project=project_id, credentials=credentials)
        require_eu_bucket(gcs_client, bucket)
    except (ValueError, GcsResidencyError) as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc
    setup_telemetry(settings)
    manager = JobManager(settings)
    mcp = create_server(manager)
    url = f"http://{settings.mcp_host}:{settings.mcp_port}{MCP_PATH}"
    logger.info("MCP Streamable HTTP listening at %s (anonymous, single process)", url)
    mcp.run(
        transport="http",
        host=settings.mcp_host,
        port=settings.mcp_port,
        path=MCP_PATH,
    )
