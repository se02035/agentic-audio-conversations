"""Environment-backed configuration for CLI and MCP."""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Load podcast creator settings from the environment and optional ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    google_cloud_project: str | None = Field(
        default=None,
        description="GCP project ID (GOOGLE_CLOUD_PROJECT). CLI can fall back to ADC.",
    )
    podcast_gcs_bucket: str | None = Field(
        default=None,
        description="EU GCS bucket name for MCP audio and status objects.",
    )
    podcast_gcs_prefix: str = Field(
        default="podcasts", description="Object prefix inside the bucket."
    )
    podcast_max_script_bytes: int = Field(
        default=512000,
        ge=1,
        description="Soft cap on YAML/JSON script payloads.",
    )
    podcast_max_concurrent_jobs: int = Field(
        default=4,
        ge=1,
        description="In-flight TTS job cap. Extra jobs stay queued in the background.",
    )
    podcast_job_stale_ttl_sec: int = Field(
        default=1800,
        ge=1,
        description="queued/running jobs with no heartbeat older than this are failed.",
    )
    podcast_job_prune_ttl_sec: int = Field(
        default=3600,
        ge=1,
        description="Drop terminal jobs from in-memory state after this many seconds.",
    )
    podcast_translate_max_chars: int = Field(
        default=8000,
        ge=1,
        description="Max characters per Cloud Translation translate_text RPC.",
    )
    mcp_host: str = Field(default="127.0.0.1", description="FastMCP HTTP bind host.")
    mcp_port: int = Field(default=8000, ge=1, le=65535, description="FastMCP HTTP bind port.")
    otel_service_name: str = Field(default="tts-podcast-creator")
    otel_traces_exporter: str = Field(
        default="console,gcp",
        description="Comma list of exporters: console, gcp. Unknown names are ignored.",
    )

    @field_validator("podcast_gcs_bucket", mode="before")
    @classmethod
    def normalize_bucket(cls, value: object) -> str | None:
        """Accept a bare bucket name or a ``gs://bucket/...`` URI."""
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if text.startswith("gs://"):
            return text[5:].split("/", 1)[0]
        return text

    @field_validator("podcast_gcs_prefix", mode="before")
    @classmethod
    def normalize_prefix(cls, value: object) -> str:
        """Strip leading/trailing slashes from the object prefix."""
        text = str(value).strip().strip("/") if value is not None else "podcasts"
        return text or "podcasts"

    def require_gcs_bucket(self) -> str:
        """Return the MCP bucket name or raise a configuration error."""
        if not self.podcast_gcs_bucket:
            raise ValueError(
                "PODCAST_GCS_BUCKET is required for the MCP server. "
                "Set it in .env to an EU-located bucket name (no gs:// prefix required)."
            )
        return self.podcast_gcs_bucket

    def job_prefix_uri(self, job_id: str) -> str:
        """Return ``gs://bucket/prefix/job_id`` for a job."""
        bucket = self.require_gcs_bucket()
        return f"gs://{bucket}/{self.podcast_gcs_prefix}/{job_id}"

    def parsed_trace_exporters(self) -> list[str]:
        """Return normalized exporter names from ``OTEL_TRACES_EXPORTER``."""
        return [
            part.strip().lower()
            for part in self.otel_traces_exporter.split(",")
            if part.strip() and part.strip().lower() not in {"none", "off"}
        ]
