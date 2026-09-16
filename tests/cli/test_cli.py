"""Tests for the Click CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from tts_audio_conversation.cli import main
from tts_audio_conversation.logic.models import ConversationScript


class TestCLI:
    """CLI command tests."""

    def test_cli_help(self) -> None:
        """Main help lists the remaining subcommands."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "template" in result.output
        assert "validate" in result.output
        assert "voices" in result.output
        assert "translate" in result.output
        assert "synthesize" in result.output
        assert "download" in result.output
        assert "status" not in result.output.split()

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

    def test_validate_command_valid_script(self, tmp_path: Path, sample_script_yaml: str) -> None:
        """Valid script reports success."""
        script_file = tmp_path / "valid.yaml"
        script_file.write_text(sample_script_yaml)
        runner = CliRunner()
        result = runner.invoke(main, ["validate", "-s", str(script_file)])
        assert result.exit_code == 0
        assert "valid" in result.output.lower()

    def test_validate_command_invalid_script(self, tmp_path: Path) -> None:
        """Invalid YAML exits non-zero."""
        script_file = tmp_path / "invalid.yaml"
        script_file.write_text("invalid_yaml: [1, 2,")
        runner = CliRunner()
        result = runner.invoke(main, ["validate", "-s", str(script_file)])
        assert result.exit_code != 0

    @patch("tts_audio_conversation.cli.list_chirp3_voices")
    @patch("tts_audio_conversation.cli.eu_tts_client")
    @patch("tts_audio_conversation.cli.get_credentials_and_project")
    def test_voices_command(
        self,
        mock_auth: MagicMock,
        mock_eu_client: MagicMock,
        mock_list: MagicMock,
    ) -> None:
        """Voices command lists API results."""
        mock_auth.return_value = (MagicMock(), "test-proj")
        mock_list.return_value = [
            {
                "name": "en-US-Chirp3-HD-Fenrir",
                "language_code": "en-US",
                "gender": "MALE",
            }
        ]
        runner = CliRunner()
        result = runner.invoke(main, ["voices", "--language", "en-US"])
        assert result.exit_code == 0
        assert "Chirp3-HD" in result.output
        assert "Fenrir" in result.output

    @patch("tts_audio_conversation.cli.get_credentials_and_project")
    @patch("tts_audio_conversation.cli.ConversationTranslator")
    def test_translate_command(
        self,
        mock_translator_cls: MagicMock,
        mock_auth: MagicMock,
        tmp_path: Path,
        sample_script_yaml: str,
        sample_german_script_dict: dict[str, Any],
    ) -> None:
        """Translate command writes a translated script."""
        mock_auth.return_value = (MagicMock(), "test-proj")
        mock_translator = MagicMock()
        mock_translator.translate_script.return_value = ConversationScript.model_validate(
            sample_german_script_dict
        )
        mock_translator_cls.return_value = mock_translator

        src_file = tmp_path / "en.yaml"
        src_file.write_text(sample_script_yaml)
        out_file = tmp_path / "de.yaml"

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "translate",
                "-s",
                str(src_file),
                "--to",
                "de-DE",
                "-o",
                str(out_file),
                "-p",
                "test-proj",
            ],
        )
        assert result.exit_code == 0
        assert out_file.exists()
        assert "de-DE" in out_file.read_text()

    @patch("tts_audio_conversation.cli.assert_voices_in_catalog")
    @patch("tts_audio_conversation.cli.storage.Client")
    @patch("tts_audio_conversation.cli.synthesize_script")
    @patch("tts_audio_conversation.cli.eu_tts_client")
    @patch("tts_audio_conversation.cli.get_credentials_and_project")
    def test_synthesize_command_local(
        self,
        mock_auth: MagicMock,
        mock_eu_client: MagicMock,
        mock_synth: MagicMock,
        mock_storage_cls: MagicMock,
        mock_catalog: MagicMock,
        tmp_path: Path,
        sample_script_yaml: str,
    ) -> None:
        """Synthesize writes a local WAV without requiring GCS."""
        mock_auth.return_value = (MagicMock(), "test-proj")
        out_file = tmp_path / "episode.wav"
        mock_synth.return_value = out_file

        script_file = tmp_path / "script.yaml"
        script_file.write_text(sample_script_yaml)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["synthesize", "-s", str(script_file), "-o", str(out_file), "-p", "test-proj"],
        )
        assert result.exit_code == 0, result.output
        mock_catalog.assert_called_once()
        mock_synth.assert_called_once()
        assert mock_synth.call_args.kwargs["output_gcs_uri"] is None
        mock_storage_cls.assert_not_called()

    def test_validate_command_rejects_oversize(
        self, tmp_path: Path, sample_script_yaml: str
    ) -> None:
        """Files larger than AUDIO_CONVERSATION_MAX_SCRIPT_BYTES are rejected before parse."""
        script_file = tmp_path / "valid.yaml"
        script_file.write_text(sample_script_yaml)
        runner = CliRunner()
        with patch("tts_audio_conversation.cli.Settings") as mock_settings:
            mock_settings.return_value.audio_conversation_max_script_bytes = 32
            result = runner.invoke(main, ["validate", "-s", str(script_file)])
        assert result.exit_code != 0
        assert "bytes" in result.output.lower()

    @patch("tts_audio_conversation.cli.assert_voices_in_catalog")
    @patch("tts_audio_conversation.cli.synthesize_script")
    @patch("tts_audio_conversation.cli.eu_tts_client")
    @patch("tts_audio_conversation.cli.get_credentials_and_project")
    def test_language_override_remaps_voices_before_catalog(
        self,
        mock_auth: MagicMock,
        mock_eu_client: MagicMock,
        mock_synth: MagicMock,
        mock_catalog: MagicMock,
        tmp_path: Path,
        sample_script_yaml: str,
    ) -> None:
        """--language remaps voices and is applied before list_voices, without translating."""
        mock_auth.return_value = (MagicMock(), "test-proj")
        out_file = tmp_path / "episode.wav"
        mock_synth.return_value = out_file
        cataloged: list[Any] = []

        def capture(_client: Any, script: ConversationScript) -> None:
            cataloged.append(script)

        mock_catalog.side_effect = capture
        script_file = tmp_path / "script.yaml"
        script_file.write_text(sample_script_yaml)
        runner = CliRunner()
        with patch("tts_audio_conversation.cli.ConversationTranslator") as mock_trans_cls:
            result = runner.invoke(
                main,
                [
                    "synthesize",
                    "-s",
                    str(script_file),
                    "-o",
                    str(out_file),
                    "-p",
                    "test-proj",
                    "--language",
                    "de-DE",
                ],
            )
        assert result.exit_code == 0, result.output
        mock_trans_cls.assert_not_called()
        assert cataloged
        checked = cataloged[0]
        assert checked.metadata.language_code == "de-DE"
        assert checked.voices["host"].name == "de-DE-Chirp3-HD-Fenrir"
        assert checked.voices["host"].language_code == "de-DE"
        assert "Welcome" in checked.turns[0].text
        submitted = mock_synth.call_args.args[1]
        assert submitted.metadata.language_code == "de-DE"
        assert submitted.voices["guest"].name == "de-DE-Chirp3-HD-Aoede"

    @patch("tts_audio_conversation.cli.assert_voices_in_catalog")
    @patch("tts_audio_conversation.cli.synthesize_script")
    @patch("tts_audio_conversation.cli.eu_tts_client")
    @patch("tts_audio_conversation.cli.get_credentials_and_project")
    def test_matching_language_and_translate_to_still_translates(
        self,
        mock_auth: MagicMock,
        mock_eu_client: MagicMock,
        mock_synth: MagicMock,
        mock_catalog: MagicMock,
        tmp_path: Path,
        sample_script_yaml: str,
        sample_german_script_dict: dict[str, Any],
    ) -> None:
        """Matching --language/--translate-to still translates from the original locale."""
        mock_auth.return_value = (MagicMock(), "test-proj")
        out_file = tmp_path / "episode.wav"
        mock_synth.return_value = out_file
        translated = ConversationScript.model_validate(sample_german_script_dict)
        script_file = tmp_path / "script.yaml"
        script_file.write_text(sample_script_yaml)
        runner = CliRunner()
        with patch("tts_audio_conversation.cli.ConversationTranslator") as mock_trans_cls:
            mock_translator = MagicMock()
            mock_translator.translate_script.return_value = translated
            mock_trans_cls.return_value = mock_translator
            result = runner.invoke(
                main,
                [
                    "synthesize",
                    "-s",
                    str(script_file),
                    "-o",
                    str(out_file),
                    "-p",
                    "test-proj",
                    "--language",
                    "de-DE",
                    "--translate-to",
                    "de-DE",
                ],
            )
        assert result.exit_code == 0, result.output
        mock_translator.translate_script.assert_called_once()
        source_script = mock_translator.translate_script.call_args.args[0]
        assert source_script.metadata.language_code == "en-US"
        assert "Welcome" in source_script.turns[0].text
        submitted = mock_synth.call_args.args[1]
        assert submitted.metadata.language_code == "de-DE"
        assert "Willkommen" in submitted.turns[0].text

    @patch("tts_audio_conversation.cli.assert_voices_in_catalog")
    @patch("tts_audio_conversation.cli.synthesize_script")
    @patch("tts_audio_conversation.cli.eu_tts_client")
    @patch("tts_audio_conversation.cli.get_credentials_and_project")
    def test_conflicting_language_and_translate_to_are_rejected(
        self,
        mock_auth: MagicMock,
        mock_eu_client: MagicMock,
        mock_synth: MagicMock,
        mock_catalog: MagicMock,
        tmp_path: Path,
        sample_script_yaml: str,
    ) -> None:
        """--language and --translate-to with different locales fail before remap."""
        mock_auth.return_value = (MagicMock(), "test-proj")
        out_file = tmp_path / "episode.wav"
        script_file = tmp_path / "script.yaml"
        script_file.write_text(sample_script_yaml)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "synthesize",
                "-s",
                str(script_file),
                "-o",
                str(out_file),
                "--language",
                "de-DE",
                "--translate-to",
                "fr-FR",
            ],
        )
        assert result.exit_code != 0
        assert "conflicts" in result.output
        mock_synth.assert_not_called()
        mock_catalog.assert_not_called()

    @patch("tts_audio_conversation.cli.download_file")
    @patch("tts_audio_conversation.cli.storage.Client")
    @patch("tts_audio_conversation.cli.get_credentials_and_project")
    def test_download_command(
        self,
        mock_auth: MagicMock,
        mock_storage_cls: MagicMock,
        mock_download: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Download command uses the official Storage helper."""
        mock_auth.return_value = (MagicMock(), "test-proj")
        mock_gcs = MagicMock()
        mock_storage_cls.return_value = mock_gcs
        out_file = tmp_path / "final.wav"

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
        mock_download.assert_called_once_with(
            mock_gcs,
            "gs://my-bucket/podcast.wav",
            out_file,
        )
