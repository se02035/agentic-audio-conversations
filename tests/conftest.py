"""Shared fixtures, mocks, and pytest traits for the tts-audio-conversation test suite."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from google.auth.credentials import Credentials

_SLOW_NODE_FRAGMENTS = (
    "test_mcp_long",
    "test_long_running_unicorn",
    "test_live_two_speaker_german_ai",
    "test_live_mcp_long_form",
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Ensure every collected test has layer traits (unit or integration), plus slow when needed."""
    for item in items:
        marks = {marker.name for marker in item.iter_markers()}
        if "integration" in Path(item.path).parts:
            if "integration" not in marks:
                item.add_marker(pytest.mark.integration)
        elif "unit" not in marks:
            item.add_marker(pytest.mark.unit)
        is_slow = any(fragment in item.nodeid for fragment in _SLOW_NODE_FRAGMENTS)
        if "slow" not in marks and is_slow:
            item.add_marker(pytest.mark.slow)


@pytest.fixture
def mock_credentials() -> MagicMock:
    """Fixture providing a mock Google ADC Credentials object."""
    creds = MagicMock(spec=Credentials)
    creds.valid = True
    creds.token = "mock-bearer-token"
    return creds


@pytest.fixture
def sample_script_dict() -> dict[str, Any]:
    """Fixture providing a valid 2-speaker conversation script dictionary."""
    return {
        "metadata": {
            "title": "Tech Pulse Europe",
            "description": "A podcast about modern cloud tech in the EU",
            "language_code": "en-US",
            "audio_encoding": "LINEAR16",
            "sample_rate_hertz": 24000,
        },
        "voices": {
            "host": {
                "name": "en-US-Chirp3-HD-Fenrir",
                "language_code": "en-US",
            },
            "guest": {
                "name": "en-US-Chirp3-HD-Aoede",
                "language_code": "en-US",
            },
        },
        "turns": [
            {
                "speaker": "host",
                "text": "Welcome to Tech Pulse Europe. Today we explore Cloud TTS.",
                "pause_after_ms": 300,
            },
            {
                "speaker": "guest",
                "text": "Hello! Excited to be on the show.",
                "pause_after_ms": 500,
            },
            {
                "speaker": "host",
                "text": "Let's dive into data sovereignty in the EU.",
            },
        ],
    }


@pytest.fixture
def sample_script_yaml(sample_script_dict: dict[str, Any]) -> str:
    """Fixture providing a valid conversation script in YAML format."""
    import yaml

    return yaml.dump(sample_script_dict, sort_keys=False)


@pytest.fixture
def sample_script_json(sample_script_dict: dict[str, Any]) -> str:
    """Fixture providing a valid conversation script in JSON format."""
    import json

    return json.dumps(sample_script_dict, indent=2)


@pytest.fixture
def sample_german_script_dict() -> dict[str, Any]:
    """Fixture providing a valid German conversation script dictionary."""
    return {
        "metadata": {
            "title": "Tech Puls Europa",
            "description": "Ein Podcast über moderne Cloud-Technologie in der EU",
            "language_code": "de-DE",
            "audio_encoding": "LINEAR16",
            "sample_rate_hertz": 24000,
        },
        "voices": {
            "host": {
                "name": "de-DE-Chirp3-HD-Fenrir",
                "language_code": "de-DE",
            },
            "guest": {
                "name": "de-DE-Chirp3-HD-Aoede",
                "language_code": "de-DE",
            },
        },
        "turns": [
            {
                "speaker": "host",
                "text": "Willkommen bei Tech Puls Europa.",
                "pause_after_ms": 300,
            },
            {
                "speaker": "guest",
                "text": "Hallo! Schön, heute dabei zu sein.",
                "pause_after_ms": 500,
            },
        ],
    }
