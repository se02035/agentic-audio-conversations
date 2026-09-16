"""Environment-backed configuration for CLI and MCP."""

from __future__ import annotations

import ipaddress

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Load audio conversation settings from the environment and optional ``.env``."""

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
    audio_conversation_gcs_staging_bucket: str | None = Field(
        default=None,
        description=(
            "GCS staging bucket for CLI and MCP (scripts, audio, status). "
            "Speech and translation use EU endpoints; EU storage residency "
            "depends on the caller selecting an EU-located bucket."
        ),
    )
    audio_conversation_gcs_prefix: str = Field(
        default="conversation", description="Object prefix inside the bucket."
    )
    audio_conversation_max_script_bytes: int = Field(
        default=512000,
        ge=1,
        description="Soft cap on YAML/JSON script payloads.",
    )
    audio_conversation_max_concurrent_jobs: int = Field(
        default=4,
        ge=1,
        description="In-flight TTS job cap. Extra jobs stay queued in the background.",
    )
    audio_conversation_job_prune_ttl_sec: int = Field(
        default=3600,
        ge=1,
        description="Drop terminal jobs from in-memory state after this many seconds.",
    )
    audio_conversation_translate_max_chars: int = Field(
        default=8000,
        ge=1,
        description="Max characters per Cloud Translation translate_text RPC.",
    )
    mcp_host: str = Field(default="127.0.0.1", description="FastMCP HTTP bind host.")
    mcp_port: int = Field(default=8000, ge=1, le=65535, description="FastMCP HTTP bind port.")
    otel_service_name: str = Field(default="tts-audio-conversation")
    otel_traces_exporter: str = Field(
        default="console,gcp",
        description="Comma list of exporters: console, gcp. Unknown names are ignored.",
    )

    @field_validator("audio_conversation_gcs_staging_bucket", mode="before")
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

    @field_validator("audio_conversation_gcs_prefix", mode="before")
    @classmethod
    def normalize_prefix(cls, value: object) -> str:
        """Strip leading/trailing slashes from the object prefix."""
        text = str(value).strip().strip("/") if value is not None else "conversation"
        return text or "conversation"

    def require_gcs_bucket(self) -> str:
        """Return the staging bucket name or raise a configuration error."""
        if not self.audio_conversation_gcs_staging_bucket:
            raise ValueError(
                "AUDIO_CONVERSATION_GCS_STAGING_BUCKET is required for the CLI and MCP server. "
                "Set it in .env to a bucket name (no gs:// prefix required). "
                "Speech and translation use EU endpoints; choose an EU-located bucket "
                "when EU storage residency is required."
            )
        return self.audio_conversation_gcs_staging_bucket

    def require_loopback_mcp_host(self) -> str:
        """Return ``mcp_host`` when it is a loopback bind, else raise."""
        host = self.mcp_host.strip()
        if not _is_loopback_mcp_host(host):
            raise ValueError(
                f"MCP_HOST '{self.mcp_host}' is not a loopback address. "
                "Unauthenticated MCP HTTP must bind to 127.0.0.1, ::1, or localhost."
            )
        return host

    def job_prefix_uri(self, job_id: str) -> str:
        """Return ``gs://bucket/prefix/jobs/job_id`` for a job."""
        bucket = self.require_gcs_bucket()
        return f"gs://{bucket}/{self.audio_conversation_gcs_prefix}/jobs/{job_id}"

    def script_prefix_uri(self, script_id: str) -> str:
        """Return ``gs://bucket/prefix/scripts/script_id`` for an uploaded script."""
        bucket = self.require_gcs_bucket()
        return f"gs://{bucket}/{self.audio_conversation_gcs_prefix}/scripts/{script_id}"

    def parsed_trace_exporters(self) -> list[str]:
        """Return normalized exporter names from ``OTEL_TRACES_EXPORTER``."""
        return [
            part.strip().lower()
            for part in self.otel_traces_exporter.split(",")
            if part.strip() and part.strip().lower() not in {"none", "off"}
        ]


def _is_loopback_mcp_host(host: str) -> bool:
    """Return True for localhost, IPv4 loopback, or IPv6 loopback binds."""
    text = host.strip()
    if len(text) >= 2 and text[0] == "[" and text[-1] == "]":
        text = text[1:-1]
    if text.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False
