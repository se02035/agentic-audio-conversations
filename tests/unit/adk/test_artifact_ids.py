"""Unit tests for collision-free ADK artifact filenames."""

from __future__ import annotations

from tts_audio_conversation.adk.artifact_ids import (
    audio_artifact_name,
    pick_uploaded_yaml_name,
    sanitize_artifact_token,
    script_artifact_name,
    upsert_audio_overview,
)


def test_script_and_audio_names_are_unique_per_ids() -> None:
    """Different MCP ids must not collapse to script.yaml / audio.wav."""
    first = script_artifact_name("11111111-1111-1111-1111-111111111111")
    second = script_artifact_name("22222222-2222-2222-2222-222222222222")
    assert first != second
    assert first.startswith("script_")
    assert first.endswith(".yaml")
    assert first != "script.yaml"
    en_audio = audio_artifact_name("job-aaa", "en-US")
    de_audio = audio_artifact_name("job-aaa", "de-DE")
    other_job = audio_artifact_name("job-bbb", "en-US")
    assert en_audio != de_audio
    assert en_audio != other_job
    assert en_audio != "audio.wav"
    assert en_audio == "audio_job-aaa_en-US.wav"


def test_sanitize_keeps_bcp47_and_strips_user_prefix() -> None:
    """``en-US`` stays intact; ``user:`` is never introduced or preserved."""
    assert sanitize_artifact_token("en-US") == "en-US"
    assert sanitize_artifact_token("de-DE") == "de-DE"
    assert not sanitize_artifact_token("en-US").startswith("user:")
    assert sanitize_artifact_token("user:secret") == "secret"
    assert sanitize_artifact_token("user:foo/bar baz") == "foo-bar-baz"
    assert sanitize_artifact_token("   ") == "unknown"
    assert sanitize_artifact_token("user:") == "unknown"


def test_pick_uploaded_yaml_prefers_chat_name() -> None:
    """Chat attachments win over canonical ``script_{id}.yaml`` artifacts."""
    names = ["script_abc.yaml", "notes.txt", "script.yaml", "script_def.yml"]
    assert pick_uploaded_yaml_name(names) == "script.yaml"
    assert pick_uploaded_yaml_name(["script_abc.yaml", "script_def.yml"]) == "script_def.yml"
    assert pick_uploaded_yaml_name(["readme.md"]) is None


def test_upsert_overview_replaces_same_job() -> None:
    """A later snapshot for the same job_id replaces the earlier row."""
    index = [{"job_id": "j1", "status": "queued"}, {"job_id": "j2", "status": "queued"}]
    updated = upsert_audio_overview(index, {"job_id": "j1", "status": "succeeded"})
    by_id = {row["job_id"]: row["status"] for row in updated}
    assert by_id == {"j1": "succeeded", "j2": "queued"}
