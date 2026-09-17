---
name: audio-conversation
description: >-
  Automated conversation and narration audio creator using Google Cloud Text-to-Speech
  Chirp 3 HD voices in the EU regional endpoint (eu-texttospeech.googleapis.com).
  Use this skill whenever the user asks to create, validate, translate, synthesize,
  or download dialogue audio, 2-speaker conversations, or single-speaker
  narration. Speech and translation use EU endpoints; EU storage residency
  depends on the caller selecting an EU-located GCS bucket.
---

# Audio Conversation Agent Skill

Orchestrate conversation/narration audio with Google Cloud TTS Chirp 3 HD on `eu-texttospeech.googleapis.com`. Translation uses `translate-eu.googleapis.com`. Prefer the library facade `AudioConversationService` (CLI and MCP are thin adapters).

`synthesizeLongAudio` does not support multi-speaker. Synthesis always uses official `TextToSpeechClient.synthesize_speech` + `multi_speaker_markup`, batches turns at ≤1500 characters, retries each batch with GAPIC `retry.Retry`, and stitches LINEAR16 WAV locally. Single-speaker scripts inject an unused `Companion` voice. Speaker aliases must be alphanumeric (`[a-zA-Z0-9]+`). Voice names must contain `Chirp3-HD`.

**Flow:** `upload_script` → optional `validate_script` / `translate_script` → `create_audio` / `start_conversation` (job only; `gs://` script URI required). Poll `get_conversation_status` / `get_job`. No MCP download tool.

## Prerequisites

1. APIs enabled: `texttospeech.googleapis.com`, `translate.googleapis.com`, `storage.googleapis.com`
2. `gcloud auth application-default login` and quota project
3. `.env` with `GOOGLE_CLOUD_PROJECT` and `AUDIO_CONVERSATION_GCS_STAGING_BUCKET` (EU bucket you choose in GCP)

## CLI workflow

```bash
uv run tts-audio-conversation template --language en-US --output script.yaml
uv run tts-audio-conversation upload --script script.yaml
# → script_uri
uv run tts-audio-conversation translate --script-uri "$SCRIPT_URI" --to de-DE
# → output_uri
uv run tts-audio-conversation synthesize --script-uri "$OUTPUT_URI" --output output/episode.wav
uv run tts-audio-conversation download --gcs-uri gs://…/jobs/{id}/output/audio.wav --output output/episode.wav
```

Details: [`docs/creating-audio.md`](../../../docs/creating-audio.md). Facade: [`docs/library-api.md`](../../../docs/library-api.md).

## MCP HTTP workflow

Anonymous Streamable HTTP at `http://127.0.0.1:8000/mcp`.

```bash
uv run tts-audio-conversation-mcp
```

Tools: `upload_script`, `validate_script`, `translate_script`, `start_conversation` (`script_uri` only), `get_conversation_status`, `cancel_conversation`.

On success, `audio_uri` is the WAV blob. Download with the caller's GCS credentials.

## ADK playground

For an interactive Web UI (not this coding-agent skill path), install `uv sync --extra adk` and follow [`docs/creating-audio.md`](../../../docs/creating-audio.md#adk-web-agent). The ADK `LlmAgent` calls MCP HTTP only; it does not call Cloud TTS or Translation itself.

## Troubleshooting

- `502` / `504`: batch over ~1500 characters. Cap is 1500; oversized turns are split.
- `429`: GAPIC retry on the batch RPC.
- Cancel applies at the next batch boundary; in-flight RPCs may finish.
- Orphaned `queued`/`running` after crash: no auto-fail / no resume — start a new job (or cancel the GCS status).
