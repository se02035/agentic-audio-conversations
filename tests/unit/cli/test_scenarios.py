"""Scenario tests for the CLI workflow (mocked facade)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from tts_audio_conversation.cli import main
from tts_audio_conversation.logic.jobs.manager import utc_now
from tts_audio_conversation.logic.jobs.models import JobRecord, JobStatus
from tts_audio_conversation.logic.results import (
    DownloadResult,
    ScriptUploadResult,
    TranslateScriptResult,
)


@pytest.fixture
def cli_runner() -> CliRunner:
    """Click CLI runner."""
    return CliRunner()


class TestConversationScenarios:
    """Core demo scenarios via the thin CLI adapter."""

    @patch("tts_audio_conversation.cli._service")
    def test_scenario_upload_translate_synthesize_download(
        self,
        mock_svc_factory: MagicMock,
        cli_runner: CliRunner,
        tmp_path: Path,
        sample_script_yaml: str,
    ) -> None:
        """Upload → translate → synthesize → download."""
        svc = MagicMock()
        mock_svc_factory.return_value = svc
        script_file = tmp_path / "dialogue_en.yaml"
        script_file.write_text(sample_script_yaml, encoding="utf-8")
        out_audio = tmp_path / "episode.wav"
        script_uri = "gs://b/conversation/scripts/s1/script.yaml"
        translated_uri = "gs://b/conversation/scripts/s1/script.de-DE.yaml"
        audio_uri = "gs://b/conversation/jobs/j1/output/audio.wav"

        svc.upload_script.return_value = ScriptUploadResult(script_uri=script_uri, script_id="s1")
        svc.translate_script.return_value = TranslateScriptResult(
            output_uri=translated_uri,
            language_code="de-DE",
            title="Demo",
            skipped=False,
        )
        queued = JobRecord(
            job_id="j1",
            status=JobStatus.queued,
            script_uri=translated_uri,
            audio_uri=audio_uri,
            status_uri="gs://b/conversation/jobs/j1/status.json",
            updated_at=utc_now(),
        )
        svc.create_audio = AsyncMock(return_value=queued)
        svc.get_job = AsyncMock(
            return_value=queued.model_copy(update={"status": JobStatus.succeeded})
        )
        svc.download.return_value = DownloadResult(local_path=out_audio, gcs_uri=audio_uri)

        assert cli_runner.invoke(main, ["upload", "-s", str(script_file)]).exit_code == 0
        assert (
            cli_runner.invoke(
                main, ["translate", "--script-uri", script_uri, "--to", "de-DE"]
            ).exit_code
            == 0
        )
        synth = cli_runner.invoke(
            main, ["synthesize", "--script-uri", translated_uri, "-o", str(out_audio)]
        )
        assert synth.exit_code == 0, synth.output
        svc.create_audio.assert_awaited_once()
        svc.download.assert_called()

    @patch("tts_audio_conversation.cli._service")
    def test_scenario_invalid_upload_fails(
        self,
        mock_svc_factory: MagicMock,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Bad local script fails upload."""
        from tts_audio_conversation.logic.exceptions import ScriptPayloadError

        svc = MagicMock()
        mock_svc_factory.return_value = svc
        svc.upload_script.side_effect = ScriptPayloadError("invalid")
        bad = tmp_path / "bad.yaml"
        bad.write_text("not: [yaml")
        result = cli_runner.invoke(main, ["upload", "-s", str(bad)])
        assert result.exit_code != 0

    def test_scenario_template_local_only(self, cli_runner: CliRunner) -> None:
        """Template does not need GCP."""
        result = cli_runner.invoke(main, ["template", "--language", "de-DE"])
        assert result.exit_code == 0
        assert "de-DE-Chirp3-HD" in result.output
