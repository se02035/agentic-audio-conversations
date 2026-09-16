---
trigger: always_on
description: >-
  Rules and invariants for Google Cloud EU data sovereignty, Text-to-Speech Chirp 3 HD regional endpoints, and test lifecycle cleanup.
---

# GCP EU Data Sovereignty & TTS Integration Rules

When writing code, scripts, CLI commands, or tests for `tts-audio-conversation`, you must adhere to the following invariants:

## 1. Strict EU Data Residency & Processing Endpoints
- **Cloud Text-to-Speech**: MUST always use the European regional endpoint `eu-texttospeech.googleapis.com`. Never route through global or US endpoints.
- **Cloud Translation**: MUST always use `translate-eu.googleapis.com` with location `europe-west1`.
- **Cloud Storage**: GCS buckets used for audio MUST be on the EU-only allowlist: `EU`, `EUR4`, `europe-central2`, `europe-north1`, `europe-north2`, `europe-southwest1`, `europe-west1`, `europe-west3`, `europe-west4`, `europe-west8`, `europe-west9`, `europe-west10`, `europe-west12`. Do not accept every `europe-*` prefix (`europe-west2` London and `europe-west6` Zürich are out). `require_eu_bucket` / `require_eu_gcs_uri` enforce this on MCP startup and CLI `synthesize --gcs-uri`. Do not construct `storage.Client()` without project + ADC.

## 2. Unified Synthesis (official SDK)
- Use `google.cloud.texttospeech.TextToSpeechClient.synthesize_speech` with `multi_speaker_markup`. Do not call `synthesizeLongAudio` (it cannot do multi-speaker).
- Retry with `google.api_core.retry.Retry` on each `synthesize_speech` call. Do not wrap the whole job in a custom retry loop.
- GCS via `google.cloud.storage.Client` and `Blob.from_uri`. Auth via `google.auth.default()`.
- Single-speaker scripts must include an unused Companion persona so `MultiSpeakerVoiceConfig` has ≥2 speakers.
- Speaker aliases must be alphanumeric (`[a-zA-Z0-9]+`).
- Batch turns at ≤1500 characters.
- Voice names must contain `Chirp3-HD`. CLI `synthesize` and MCP `start_conversation` also call EU `list_voices` (fail fast). `validate_script` is schema-only.

## 3. Test Lifecycle & Resource Cleanup
- Tests that create GCS blobs must delete them in `finally` unless `KEEP_TEST_ARTIFACTS=true`.
- Do not commit `.wav` / `.mp3` media. Live integration tests in `tests/integration/` must keep passing (library synthesis and MCP HTTP smoke).

## 4. CLI Execution
- Executable: `tts-audio-conversation` (alias `audio-conversation`) or `python -m tts_audio_conversation.cli`.
- MCP HTTP: `tts-audio-conversation-mcp` or `python -m tts_audio_conversation.mcp` at `http://127.0.0.1:8000/mcp` (anonymous).
- Project flag is `--project`. Synthesize writes a local WAV (`--output`); `--gcs-uri` is optional.
