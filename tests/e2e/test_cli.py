"""E2E CLI adapter tests against live GCP (skipped without ADC/.env)."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest
from click.testing import CliRunner
from dotenv import load_dotenv

from tts_audio_conversation.cli import main

load_dotenv()


def _require_cli_env() -> None:
    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        pytest.skip("GOOGLE_CLOUD_PROJECT must be set for CLI e2e tests")
    if not os.environ.get("AUDIO_CONVERSATION_GCS_STAGING_BUCKET") and not os.environ.get(
        "AUDIO_CONVERSATION_TEST_GCS_URI"
    ):
        pytest.skip("Staging bucket env must be set for CLI e2e tests")


@pytest.mark.e2e
def test_cli_template_and_help() -> None:
    """Local-only CLI commands work without GCP."""
    runner = CliRunner()
    help_result = runner.invoke(main, ["--help"])
    assert help_result.exit_code == 0
    assert "upload" in help_result.output
    assert "synthesize" in help_result.output
    tmpl = runner.invoke(main, ["template", "--language", "en-US"])
    assert tmpl.exit_code == 0
    assert "Chirp3-HD" in tmpl.output


@pytest.mark.e2e
def test_cli_upload_validate_synthesize_download(tmp_path: Path) -> None:
    """CLI upload → validate → synthesize (-o) against live staging."""
    _require_cli_env()
    # Prefer explicit staging bucket for Settings() inside CLI.
    if not os.environ.get("AUDIO_CONVERSATION_GCS_STAGING_BUCKET"):
        test_uri = os.environ["AUDIO_CONVERSATION_TEST_GCS_URI"]
        os.environ["AUDIO_CONVERSATION_GCS_STAGING_BUCKET"] = test_uri[5:].split("/", 1)[0]
    os.environ["AUDIO_CONVERSATION_GCS_PREFIX"] = f"conversation/cli_e2e_{uuid.uuid4().hex[:8]}"

    script = tmp_path / "episode.yaml"
    script.write_text(
        """
metadata:
  title: CLI E2E
  language_code: en-US
voices:
  host:
    name: en-US-Chirp3-HD-Fenrir
    language_code: en-US
turns:
  - speaker: host
    text: CLI end to end upload and synthesize.
  - speaker: host
    text: Checking the thin adapter path.
""".strip()
        + "\n",
        encoding="utf-8",
    )
    out_wav = tmp_path / "episode.wav"
    runner = CliRunner()

    uploaded = runner.invoke(main, ["upload", "-s", str(script)])
    assert uploaded.exit_code == 0, uploaded.output
    # Rich may soft-wrap the URI across lines; normalize whitespace first.
    flat = re.sub(r"\s+", "", uploaded.output)
    match = re.search(r"gs://[^\s]+/script\.yaml", flat)
    assert match, uploaded.output
    script_uri = match.group(0)

    validated = runner.invoke(main, ["validate", "--script-uri", script_uri])
    assert validated.exit_code == 0, validated.output
    assert "valid" in validated.output.lower()

    synth = runner.invoke(
        main,
        ["synthesize", "--script-uri", script_uri, "-o", str(out_wav)],
    )
    assert synth.exit_code == 0, synth.output
    assert out_wav.exists()
    assert out_wav.stat().st_size > 0
