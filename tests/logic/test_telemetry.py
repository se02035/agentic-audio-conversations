"""OpenTelemetry setup writes console spans to stderr."""

from __future__ import annotations

from tts_audio_conversation.logic.settings import Settings
from tts_audio_conversation.logic.telemetry import setup_telemetry


def test_console_exporter_prints_spans(capsys: object) -> None:
    """A span created after setup_telemetry(console) is printed on stderr."""
    provider = setup_telemetry(
        Settings(google_cloud_project="test-proj", otel_traces_exporter="console")
    )
    tracer = provider.get_tracer("tests.telemetry")
    with tracer.start_as_current_span("start_conversation"):
        pass
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "start_conversation" in captured.err
    assert "[span]" in captured.err
