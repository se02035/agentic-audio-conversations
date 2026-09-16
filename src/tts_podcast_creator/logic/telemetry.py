"""OpenTelemetry traces to stderr and optionally Google Cloud Trace."""

from __future__ import annotations

import logging
import os
import sys
from typing import IO, Any, cast

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from tts_podcast_creator.logic.settings import Settings

logger = logging.getLogger(__name__)
_PROVIDER_SET = False


class _CurrentStderr:
    """Write to the current ``sys.stderr`` so pytest capture cannot pin a closed file."""

    def write(self, message: str) -> int:
        """Write ``message`` to stderr, ignoring a closed capture stream."""
        try:
            written = sys.stderr.write(message)
        except ValueError:
            return 0
        return written if isinstance(written, int) else len(message)

    def flush(self) -> None:
        """Flush stderr if it is still open."""
        try:
            sys.stderr.flush()
        except ValueError:
            return


def setup_telemetry(settings: Settings | None = None) -> TracerProvider:
    """Configure a global ``TracerProvider`` once.

    Console spans go to stderr (safe for HTTP JSON-RPC). Cloud Trace uses ADC
    when ``gcp`` is listed in ``OTEL_TRACES_EXPORTER``. Missing GCP credentials
    log a warning and skip that exporter.

    When a provider is already installed, a new provider is still created and
    returned so callers (and tests) can use the requested exporters even if
    ``_PROVIDER_SET`` is already true.

    Args:
        settings: Optional settings. Loaded from the environment when omitted.

    Returns:
        The ``TracerProvider`` configured for this call.
    """
    global _PROVIDER_SET
    cfg = settings or Settings()
    resource = _build_resource(cfg)
    provider = TracerProvider(resource=resource)
    exporters = cfg.parsed_trace_exporters()

    if "console" in exporters:
        provider.add_span_processor(
            SimpleSpanProcessor(
                ConsoleSpanExporter(
                    out=cast(IO[str], _CurrentStderr()),
                    formatter=_format_console_span,
                )
            )
        )
    if "gcp" in exporters:
        gcp_exporter = _cloud_trace_exporter(cfg)
        if gcp_exporter is not None:
            provider.add_span_processor(BatchSpanProcessor(gcp_exporter))

    if not _PROVIDER_SET:
        trace.set_tracer_provider(provider)
        _PROVIDER_SET = True
        logger.info("OpenTelemetry tracing enabled (%s)", ", ".join(exporters) or "no exporters")
    return provider


def _build_resource(settings: Settings) -> Resource:
    attributes: dict[str, str] = {"service.name": settings.otel_service_name}
    if settings.google_cloud_project:
        attributes["gcp.project_id"] = settings.google_cloud_project
    base = Resource.create(attributes)
    on_gcp = bool(
        os.environ.get("K_SERVICE")
        or os.environ.get("KUBERNETES_SERVICE_HOST")
        or os.environ.get("GCE_METADATA_HOST")
    )
    if not on_gcp:
        return base
    try:
        from opentelemetry.resourcedetector.gcp_resource_detector import GoogleCloudResourceDetector

        detected = GoogleCloudResourceDetector().detect()
        return base.merge(detected)
    except Exception as exc:  # pragma: no cover - metadata server is optional
        logger.debug("GCP resource detector skipped: %s", exc)
        return base


def _cloud_trace_exporter(settings: Settings) -> Any | None:
    try:
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

        return CloudTraceSpanExporter(project_id=settings.google_cloud_project)  # type: ignore[no-untyped-call]
    except Exception as exc:
        logger.warning("Cloud Trace exporter not configured: %s", exc)
        return None


def _format_console_span(span: Any) -> str:
    """Compact one-line span dump for local terminal debugging."""
    ctx = span.get_span_context()
    status = getattr(span.status, "status_code", None)
    return (
        f"[span] name={span.name} "
        f"trace_id={ctx.trace_id:032x} "
        f"span_id={ctx.span_id:016x} "
        f"status={status}\n"
    )
