"""Tests for the Click CLI (thin adapter over AudioConversationService)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from tts_audio_conversation.cli import main
from tts_audio_conversation.logic.jobs.manager import utc_now
from tts_audio_conversation.logic.jobs.models import JobRecord, JobStatus
from tts_audio_conversation.logic.results import (
    DownloadResult,
    ScriptUploadResult,
    ScriptValidationResult,
    TranslateScriptResult,
    VoiceInfo,
)


def _mock_service() -> MagicMock:
    return MagicMock()


class TestCLI:
    """CLI command tests."""

    def test_cli_help(self) -> None:
        """Main help lists the facade-backed subcommands."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        for name in (
            "template",
            "upload",
            "validate",
            "voices",
            "translate",
            "synthesize",
            "status",
            "download",
        ):
            assert name in result.output

    def test_template_command_stdout_yaml(self) -> None:
        """YAML template prints Chirp 3 voices."""
        runner = CliRunner()
        result = runner.invoke(main, ["template", "--language", "en-US"])
        assert result.exit_code == 0
        assert "metadata:" in result.output
        assert "en-US-Chirp3-HD-Fenrir" in result.output
        assert "LINEAR16" in result.output

    def test_template_command_file_json(self, tmp_path: Path) -> None:
        """JSON template writes a file."""
        runner = CliRunner()
        out_file = tmp_path / "script.json"
        result = runner.invoke(main, ["template", "--format", "json", "--output", str(out_file)])
        assert result.exit_code == 0
        assert out_file.exists()
        assert '"title":' in out_file.read_text()

    @patch("tts_audio_conversation.cli._service")
    def test_upload_command(self, mock_svc_factory: MagicMock, tmp_path: Path) -> None:
        """Upload prints script_uri from the facade."""
        svc = _mock_service()
        mock_svc_factory.return_value = svc
        svc.upload_script.return_value = ScriptUploadResult(
            script_uri="gs://b/conversation/scripts/id1/script.yaml",
            script_id="id1",
        )
        script_file = tmp_path / "ep.yaml"
        script_file.write_text("x: 1")
        runner = CliRunner()
        result = runner.invoke(main, ["upload", "-s", str(script_file), "-p", "proj"])
        assert result.exit_code == 0, result.output
        assert "gs://b/conversation/scripts/id1/script.yaml" in result.output
        svc.upload_script.assert_called_once()

    @patch("tts_audio_conversation.cli._service")
    def test_validate_command_valid(self, mock_svc_factory: MagicMock) -> None:
        """Validate prints metrics for a valid gs:// script."""
        svc = _mock_service()
        mock_svc_factory.return_value = svc
        svc.validate_script.return_value = ScriptValidationResult(
            valid=True,
            title="Demo",
            language_code="en-US",
            speakers=["host"],
            turn_count=2,
            total_characters=100,
            script_uri="gs://b/conversation/scripts/id1/script.yaml",
        )
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["validate", "--script-uri", "gs://b/conversation/scripts/id1/script.yaml"],
        )
        assert result.exit_code == 0, result.output
        assert "valid" in result.output.lower()
        assert "Demo" in result.output

    @patch("tts_audio_conversation.cli._service")
    def test_validate_command_invalid(self, mock_svc_factory: MagicMock) -> None:
        """Invalid URI validation exits non-zero."""
        svc = _mock_service()
        mock_svc_factory.return_value = svc
        svc.validate_script.return_value = ScriptValidationResult(
            valid=False,
            script_uri="gs://b/x",
            error="boom",
        )
        runner = CliRunner()
        result = runner.invoke(main, ["validate", "--script-uri", "gs://b/x"])
        assert result.exit_code != 0
        assert "boom" in result.output

    @patch("tts_audio_conversation.cli._service")
    def test_voices_command(self, mock_svc_factory: MagicMock) -> None:
        """Voices command lists facade results."""
        svc = _mock_service()
        mock_svc_factory.return_value = svc
        svc.list_voices.return_value = [
            VoiceInfo(name="en-US-Chirp3-HD-Fenrir", language_code="en-US", gender="MALE")
        ]
        runner = CliRunner()
        result = runner.invoke(main, ["voices", "--language", "en-US"])
        assert result.exit_code == 0
        assert "Fenrir" in result.output

    @patch("tts_audio_conversation.cli._service")
    def test_translate_command(self, mock_svc_factory: MagicMock) -> None:
        """Translate prints output_uri."""
        svc = _mock_service()
        mock_svc_factory.return_value = svc
        svc.translate_script.return_value = TranslateScriptResult(
            output_uri="gs://b/conversation/scripts/id1/script.de-DE.yaml",
            language_code="de-DE",
            title="Demo",
            skipped=False,
        )
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "translate",
                "--script-uri",
                "gs://b/conversation/scripts/id1/script.yaml",
                "--to",
                "de-DE",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "script.de-DE.yaml" in result.output

    @patch("tts_audio_conversation.cli._service")
    def test_synthesize_command_polls_and_downloads(
        self, mock_svc_factory: MagicMock, tmp_path: Path
    ) -> None:
        """Synthesize starts a job, polls until succeeded, then downloads."""
        svc = _mock_service()
        mock_svc_factory.return_value = svc
        job = JobRecord(
            job_id="j1",
            status=JobStatus.queued,
            script_uri="gs://b/conversation/scripts/id1/script.yaml",
            audio_uri="gs://b/conversation/jobs/j1/output/audio.wav",
            status_uri="gs://b/conversation/jobs/j1/status.json",
            updated_at=utc_now(),
        )
        done = job.model_copy(update={"status": JobStatus.succeeded})
        svc.create_audio = AsyncMock(return_value=job)
        svc.get_job = AsyncMock(return_value=done)
        out = tmp_path / "out.wav"
        svc.download.return_value = DownloadResult(local_path=out, gcs_uri=done.audio_uri)

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "synthesize",
                "--script-uri",
                job.script_uri,
                "-o",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        svc.create_audio.assert_awaited_once()
        svc.download.assert_called_once()

    @patch("tts_audio_conversation.cli._service")
    def test_download_command(self, mock_svc_factory: MagicMock, tmp_path: Path) -> None:
        """Download command uses the facade."""
        svc = _mock_service()
        mock_svc_factory.return_value = svc
        out_file = tmp_path / "final.wav"
        svc.download.return_value = DownloadResult(
            local_path=out_file,
            gcs_uri="gs://my-bucket/podcast.wav",
        )
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "download",
                "-g",
                "gs://my-bucket/podcast.wav",
                "-o",
                str(out_file),
                "-p",
                "test-proj",
            ],
        )
        assert result.exit_code == 0
        svc.download.assert_called_once()
