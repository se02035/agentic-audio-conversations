"""Tests for starter script templates."""

from __future__ import annotations

import logging

import pytest

from tts_podcast_creator.logic.template import SAMPLE_TURNS_BY_LANG, create_script_template


def test_create_script_template_copies_turns() -> None:
    """Mutating a generated script must not alter the module-level sample turns."""
    original = SAMPLE_TURNS_BY_LANG["en"][0].text
    script = create_script_template("en-US")
    script.turns[0].text = "mutated locally"
    later = create_script_template("en-US")
    assert SAMPLE_TURNS_BY_LANG["en"][0].text == original
    assert later.turns[0].text == original
    assert script.turns[0].text == "mutated locally"


def test_create_script_template_falls_back_to_english(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unknown language prefixes log a fallback and reuse English sample turns."""
    with caplog.at_level(logging.WARNING, logger="tts_podcast_creator.logic.template"):
        script = create_script_template("es-ES")
    assert "falling back to English" in caplog.text
    assert "es" in caplog.text
    assert script.metadata.language_code == "es-ES"
    assert script.voices["host"].name.startswith("es-ES-Chirp3-HD")
    assert script.turns[0].text == SAMPLE_TURNS_BY_LANG["en"][0].text
