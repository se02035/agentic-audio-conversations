"""Parse ADK API-server JSON events without starting the server."""

from __future__ import annotations

from tests.unit.adk.api_events import (
    artifact_delta_filenames,
    function_call_names,
    function_responses,
    inline_file_part,
)


def test_function_call_names_accepts_camel_case_run_payload() -> None:
    """``POST /run`` events use camelCase functionCall parts."""
    events = [
        {
            "content": {
                "role": "model",
                "parts": [
                    {"functionCall": {"id": "fc-1", "name": "ingest_uploaded_script"}},
                    {"functionCall": {"id": "fc-2", "name": "validate_script"}},
                ],
            }
        }
    ]
    assert function_call_names(events) == {"ingest_uploaded_script", "validate_script"}


def test_function_responses_read_session_events() -> None:
    """``GET session`` wraps events; functionResponse payloads stay dicts."""
    session = {
        "events": [
            {
                "content": {
                    "parts": [
                        {
                            "functionResponse": {
                                "id": "fc-9",
                                "name": "create_audio_conversation",
                                "response": {"status": "queued", "job_id": "j1"},
                            }
                        }
                    ]
                }
            }
        ]
    }
    found = function_responses(session, "create_audio_conversation")
    assert len(found) == 1
    assert found[0]["response"]["status"] == "queued"


def test_artifact_delta_filenames_reads_camel_case_actions() -> None:
    """ADK Web lists artifacts from ``actions.artifactDelta``, not GET /artifacts."""
    session = {
        "events": [
            {"actions": {"artifactDelta": {"unicorn_fairytale.yaml": 0}}},
            {"actions": {"artifact_delta": {"audio_job_en-US.wav": 0}}},
        ]
    }
    assert artifact_delta_filenames(session) == {
        "unicorn_fairytale.yaml",
        "audio_job_en-US.wav",
    }


def test_inline_file_part_is_base64() -> None:
    """``POST /run`` file parts carry base64 inlineData."""
    part = inline_file_part("script.yaml", b"hello", "application/yaml")
    assert part["inlineData"]["displayName"] == "script.yaml"
    assert part["inlineData"]["data"] == "aGVsbG8="
