"""Scenario tests for the CLI workflow (mocked GCP)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from tts_podcast_creator.cli import main
from tts_podcast_creator.logic.models import PodcastScript


@pytest.fixture
def cli_runner() -> CliRunner:
    """Click CLI runner."""
    return CliRunner()


@pytest.fixture
def mock_auth() -> Any:
    """Mock ADC + project resolution used by the CLI."""
    with patch(
        "tts_podcast_creator.cli.get_credentials_and_project",
        return_value=(MagicMock(), "test-eu-project"),
    ):
        yield


@pytest.fixture(autouse=True)
def mock_voice_catalog_and_eu_writes() -> Any:
    """Skip live list_voices and treat GCS writes as EU in CLI scenario tests."""
    with (
        patch("tts_podcast_creator.cli.assert_voices_in_catalog"),
        patch("tts_podcast_creator.cli.require_eu_gcs_uri"),
    ):
        yield


class TestPodcastScenarios:
    """Core demo scenarios."""

    def test_scenario_1_english_to_german_translation_synthesis_download(
        self,
        cli_runner: CliRunner,
        mock_auth: None,
        tmp_path: Path,
        sample_script_yaml: str,
        sample_german_script_dict: dict[str, Any],
    ) -> None:
        """Translate English script to German, synthesize, then download."""
        script_file = tmp_path / "dialogue_en.yaml"
        script_file.write_text(sample_script_yaml, encoding="utf-8")
        out_audio_file = tmp_path / "podcast_de.wav"
        gcs_uri = "gs://my-eu-bucket/podcasts/german_episode.wav"
        translated_script = PodcastScript.model_validate(sample_german_script_dict)

        with (
            patch("tts_podcast_creator.cli.PodcastTranslator") as mock_trans_cls,
            patch("tts_podcast_creator.cli.synthesize_script") as mock_synth,
            patch("tts_podcast_creator.cli.eu_tts_client"),
            patch("tts_podcast_creator.cli.storage.Client") as mock_storage_cls,
            patch("tts_podcast_creator.cli.download_file") as mock_download,
        ):
            mock_translator = MagicMock()
            mock_translator.translate_script.return_value = translated_script
            mock_trans_cls.return_value = mock_translator
            mock_synth.return_value = out_audio_file
            mock_gcs = MagicMock()
            mock_storage_cls.return_value = mock_gcs

            synth_result = cli_runner.invoke(
                main,
                [
                    "synthesize",
                    "--script",
                    str(script_file),
                    "--output",
                    str(out_audio_file),
                    "--gcs-uri",
                    gcs_uri,
                    "--translate-to",
                    "de-DE",
                ],
            )
            assert synth_result.exit_code == 0, synth_result.output
            mock_translator.translate_script.assert_called_once()
            mock_synth.assert_called_once()
            submitted = mock_synth.call_args.args[1]
            assert submitted.metadata.language_code == "de-DE"
            assert mock_synth.call_args.kwargs["output_gcs_uri"] == gcs_uri

            down_result = cli_runner.invoke(
                main,
                ["download", "--gcs-uri", gcs_uri, "--output", str(out_audio_file)],
            )
            assert down_result.exit_code == 0, down_result.output
            mock_download.assert_called_once()

    def test_scenario_2_german_template_with_custom_voice_no_translation(
        self,
        cli_runner: CliRunner,
        mock_auth: None,
        tmp_path: Path,
    ) -> None:
        """German script with --translate-to de-DE must not call Translation."""
        german_custom_yaml = """
metadata:
  title: "Deutscher Tech Podcast"
  language_code: "de-DE"
  audio_encoding: "LINEAR16"
voices:
  moderator:
    name: "de-DE-Chirp3-HD-Puck"
    language_code: "de-DE"
  experte:
    name: "de-DE-Chirp3-HD-Aoede"
    language_code: "de-DE"
turns:
  - speaker: "moderator"
    text: "Willkommen zum heutigen Tech-Talk!"
    pause_after_ms: 300
  - speaker: "experte"
    text: "Guten Tag, danke für die Einladung."
    pause_after_ms: 400
"""
        script_file = tmp_path / "dialogue_de_custom.yaml"
        script_file.write_text(german_custom_yaml, encoding="utf-8")
        out_audio_file = tmp_path / "german_custom.wav"
        gcs_uri = "gs://custom-eu-bucket/podcasts/german_custom.wav"

        with (
            patch("tts_podcast_creator.cli.PodcastTranslator") as mock_trans_cls,
            patch("tts_podcast_creator.cli.synthesize_script") as mock_synth,
            patch("tts_podcast_creator.cli.eu_tts_client"),
            patch("tts_podcast_creator.cli.storage.Client"),
        ):
            mock_synth.return_value = out_audio_file
            synth_result = cli_runner.invoke(
                main,
                [
                    "synthesize",
                    "--script",
                    str(script_file),
                    "--output",
                    str(out_audio_file),
                    "--gcs-uri",
                    gcs_uri,
                    "--translate-to",
                    "de-DE",
                ],
            )
            assert synth_result.exit_code == 0, synth_result.output
            assert "skipping translation" in synth_result.output
            mock_trans_cls.return_value.translate_script.assert_not_called()
            submitted = mock_synth.call_args.args[1]
            assert submitted.voices["moderator"].name == "de-DE-Chirp3-HD-Puck"
            assert submitted.voices["experte"].name == "de-DE-Chirp3-HD-Aoede"

    def test_scenario_3_invalid_podcast_template_fails_validation(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Undeclared speaker fails validation with a non-zero exit."""
        invalid_yaml = """
metadata:
  title: "Broken Script"
  language_code: "en-US"
voices:
  host:
    name: "en-US-Chirp3-HD-Fenrir"
    language_code: "en-US"
turns:
  - speaker: "ghostspeaker"
    text: "I do not exist in the voice mapping."
"""
        invalid_file = tmp_path / "broken_script.yaml"
        invalid_file.write_text(invalid_yaml, encoding="utf-8")
        result = cli_runner.invoke(main, ["validate", "--script", str(invalid_file)])
        assert result.exit_code == 1
        assert "validation failed" in result.output.lower()
        assert "ghostspeaker" in result.output.lower()

    def test_scenario_4_single_person_podcast_synthesis_and_download(
        self,
        cli_runner: CliRunner,
        mock_auth: None,
        tmp_path: Path,
    ) -> None:
        """Single-narrator script validates and synthesizes."""
        solo_yaml = """
metadata:
  title: "Solo Monologue Deep Dive"
  language_code: "de-DE"
  audio_encoding: "LINEAR16"
voices:
  narrator:
    name: "de-DE-Chirp3-HD-Fenrir"
    language_code: "de-DE"
turns:
  - speaker: "narrator"
    text: "Willkommen zu meinem Solo-Podcast über künstliche Intelligenz."
    pause_after_ms: 500
  - speaker: "narrator"
    text: "Heute analysieren wir die neuesten Entwicklungen in Europa."
    pause_after_ms: 400
"""
        solo_file = tmp_path / "solo_podcast.yaml"
        solo_file.write_text(solo_yaml, encoding="utf-8")
        out_audio_file = tmp_path / "solo_audio.wav"

        val_result = cli_runner.invoke(main, ["validate", "--script", str(solo_file)])
        assert val_result.exit_code == 0
        assert "Script is valid" in val_result.output
        assert "Speakers (1): narrator" in val_result.output

        with (
            patch("tts_podcast_creator.cli.synthesize_script") as mock_synth,
            patch("tts_podcast_creator.cli.eu_tts_client"),
        ):
            mock_synth.return_value = out_audio_file
            synth_result = cli_runner.invoke(
                main,
                [
                    "synthesize",
                    "--script",
                    str(solo_file),
                    "--output",
                    str(out_audio_file),
                ],
            )
            assert synth_result.exit_code == 0
            submitted = mock_synth.call_args.args[1]
            assert len(submitted.voices) == 1
            assert "narrator" in submitted.voices

    def test_scenario_long_running_fairytale_workflow(
        self,
        cli_runner: CliRunner,
        mock_auth: None,
        tmp_path: Path,
    ) -> None:
        """Validate the static ~15-minute fairytale template and mock-synthesize it."""
        repo_root = Path(__file__).resolve().parents[2]
        template_path = repo_root / "templates" / "unicorn_fairytale.yaml"
        assert template_path.exists(), f"Static template not found at {template_path}"

        content = template_path.read_text(encoding="utf-8")
        script = PodcastScript.from_yaml(content)
        assert len(script.voices) == 1
        assert "narrator" in script.voices
        total_words = sum(len(turn.text.split()) for turn in script.turns)
        assert 2000 <= total_words <= 2500, f"Expected ~2,150 words, got {total_words}"

        val_result = cli_runner.invoke(main, ["validate", "--script", str(template_path)])
        assert val_result.exit_code == 0
        assert "Script is valid" in val_result.output
        assert "Dialogue Turns: 23" in val_result.output

        out_audio_file = tmp_path / "unicorn_fairytale.wav"
        with (
            patch("tts_podcast_creator.cli.synthesize_script") as mock_synth,
            patch("tts_podcast_creator.cli.eu_tts_client"),
        ):
            mock_synth.return_value = out_audio_file
            synth_result = cli_runner.invoke(
                main,
                [
                    "synthesize",
                    "--script",
                    str(template_path),
                    "--output",
                    str(out_audio_file),
                ],
            )
            assert synth_result.exit_code == 0
            submitted = mock_synth.call_args.args[1]
            assert len(submitted.turns) == 23
