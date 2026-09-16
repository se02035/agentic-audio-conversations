---
trigger: always_on
description: >-
  Rules and invariants for Google Cloud EU ML processing endpoints, Chirp 3 HD TTS,
  and test lifecycle cleanup.
---

# GCP EU Processing & TTS Integration Rules

When writing code, scripts, CLI commands, or tests for `tts-audio-conversation`, adhere to:

## 1. EU ML processing endpoints
- **Cloud Text-to-Speech**: always `eu-texttospeech.googleapis.com`. Never global/US TTS.
- **Cloud Translation**: always `translate-eu.googleapis.com` with location `europe-west1`.
- **Cloud Storage**: callers choose an EU bucket in GCP. The library does **not** enforce bucket residency on writes. Do not construct `storage.Client()` without project + ADC.

## 2. Unified synthesis (official SDK)
- Use `TextToSpeechClient.synthesize_speech` with `multi_speaker_markup`. Never `synthesizeLongAudio`.
- Retry each `synthesize_speech` with GAPIC `retry.Retry`.
- GCS via `google.cloud.storage.Client` and `Blob.from_uri`. Auth via `google.auth.default()`.
- Single-speaker scripts inject an unused Companion persona (≥2 speaker definitions).
- Speaker aliases `[a-zA-Z0-9]+`. Voice names must contain `Chirp3-HD`.
- Batch turns at ≤1500 characters.
- `create_audio` / `start_conversation` call EU `list_voices` (fail fast). `validate_script` is schema-only.

## 3. Facade / adapters
- Prefer `AudioConversationService` for orchestration. CLI and MCP must not call batching, voice asserts, translator, or TTS directly.
- Flow: `upload_script` → optional `translate_script` → `create_audio` (`gs://` URI only). Job-only audio creation.
- No stale auto-fail / no TTS resume. Cancel after restart writes GCS `cancelled` only.

## 4. Test lifecycle
- Tests that create GCS blobs must delete them in `finally` unless `KEEP_TEST_ARTIFACTS=true`.
- Do not commit `.wav` / `.mp3` media.

## 5. Entrypoints
- CLI: `tts-audio-conversation` (alias `audio-conversation`) or `python -m tts_audio_conversation.cli`.
- MCP HTTP: `tts-audio-conversation-mcp` or `python -m tts_audio_conversation.mcp` at `http://127.0.0.1:8000/mcp` (anonymous, loopback).
- Project flag is `--project`.
