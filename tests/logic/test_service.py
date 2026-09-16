"""Unit tests for AudioConversationService."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.mcp.helpers import instant_synth, make_service
from tts_audio_conversation.logic.exceptions import ScriptPayloadError
from tts_audio_conversation.logic.models import ConversationScript
from tts_audio_conversation.logic.results import TranslateScriptResult


def test_upload_script_writes_yaml(sample_script_yaml: str) -> None:
    """upload_script stores normalized YAML under scripts/{id}/script.yaml."""
    service, _manager, gcs = make_service(instant_synth)
    result = service.upload_script(sample_script_yaml, script_id="fixed-id")
    assert result.script_id == "fixed-id"
    assert result.script_uri.endswith("/scripts/fixed-id/script.yaml")
    assert result.script_uri in gcs.objects
    assert b"metadata:" in gcs.objects[result.script_uri]


def test_validate_script_soft_fails_missing() -> None:
    """Missing objects return valid=False without raising."""
    service, _manager, _gcs = make_service(instant_synth)
    result = service.validate_script("gs://test-eu-bucket/conversation/scripts/missing/script.yaml")
    assert result.valid is False
    assert result.error


def test_translate_script_writes_sibling(
    sample_script_yaml: str, sample_german_script_dict: dict[str, Any]
) -> None:
    """translate_script uploads script.{lang}.yaml and returns output_uri."""
    german = ConversationScript.model_validate(sample_german_script_dict)

    def translate(script: ConversationScript, target: str) -> ConversationScript:
        assert target == "de-DE"
        assert script.metadata.language_code == "en-US"
        return german

    service, _manager, gcs = make_service(instant_synth, translate_fn=translate)
    uploaded = service.upload_script(sample_script_yaml, script_id="s1")
    result = service.translate_script(uploaded.script_uri, "de-DE")
    assert isinstance(result, TranslateScriptResult)
    assert result.skipped is False
    assert result.output_uri.endswith("/script.de-DE.yaml")
    assert result.output_uri in gcs.objects


def test_translate_script_skips_when_already_target(sample_script_yaml: str) -> None:
    """Already-matching language skips RPC and returns the input URI."""
    service, _manager, _gcs = make_service(instant_synth)
    uploaded = service.upload_script(sample_script_yaml, script_id="s1")
    result = service.translate_script(uploaded.script_uri, "en-US")
    assert result.skipped is True
    assert result.output_uri == uploaded.script_uri


@pytest.mark.asyncio
async def test_create_audio_requires_gs_uri() -> None:
    """create_audio rejects non-gs URIs."""
    service, _manager, _gcs = make_service(instant_synth)
    with pytest.raises(ScriptPayloadError):
        await service.create_audio("/local/path.yaml")


def test_download_writes_local_file(tmp_path: Path) -> None:
    """Download copies FakeGcs bytes to a local path."""
    service, _manager, gcs = make_service(instant_synth)
    uri = "gs://test-eu-bucket/conversation/jobs/j1/output/audio.wav"
    gcs.objects[uri] = b"RIFFDATA"
    dest = tmp_path / "out.wav"
    result = service.download(uri, dest)
    assert result.local_path == dest
    assert dest.read_bytes() == b"RIFFDATA"
