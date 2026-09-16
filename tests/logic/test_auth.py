"""Tests for ADC and GCP project resolution."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from google.auth.exceptions import DefaultCredentialsError

from tts_audio_conversation.logic.auth import get_credentials_and_project, resolve_project_id


class TestAuth:
    """GCP credential and project resolution."""

    def test_resolve_project_id_from_flag(self) -> None:
        """CLI flag wins over the environment."""
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "env-project"}):
            project = resolve_project_id(project_override="flag-project")
            assert project == "flag-project"

    def test_resolve_project_id_from_env(self) -> None:
        """GOOGLE_CLOUD_PROJECT is used when no flag is set."""
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "env-project"}):
            project = resolve_project_id(project_override=None)
            assert project == "env-project"

    def test_resolve_project_id_from_adc(self) -> None:
        """ADC project is used when flag and env are absent."""
        with patch.dict(os.environ, {}, clear=True):
            project = resolve_project_id(project_override=None, adc_project="adc-project")
            assert project == "adc-project"

    def test_resolve_project_id_missing_raises(self) -> None:
        """ValueError when nothing provides a project."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="GCP Project ID could not be determined"):
                resolve_project_id(project_override=None, adc_project=None)

    def test_get_credentials_and_project_success(self, mock_credentials: MagicMock) -> None:
        """Single google.auth.default() call returns creds and project."""
        with patch(
            "tts_audio_conversation.logic.auth.google.auth.default",
            return_value=(mock_credentials, "adc-project"),
        ):
            with patch.dict(os.environ, {}, clear=True):
                creds, project = get_credentials_and_project()
        assert creds is mock_credentials
        assert project == "adc-project"

    def test_get_credentials_and_project_missing_raises(self) -> None:
        """DefaultCredentialsError when ADC is missing."""
        with patch(
            "tts_audio_conversation.logic.auth.google.auth.default",
            side_effect=DefaultCredentialsError("No credentials"),
        ):
            with pytest.raises(DefaultCredentialsError):
                get_credentials_and_project()
