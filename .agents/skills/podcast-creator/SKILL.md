---
name: podcast-creator
description: >-
  Automated podcast and narration audio creator using Google Cloud Text-to-Speech
  Chirp 3 HD voices in the EU regional endpoint (eu-texttospeech.googleapis.com).
  Use this skill whenever the user asks to create, validate, translate, synthesize,
  or download podcasts, dialogue audio, 2-speaker conversations, or single-speaker
  narration with full EU data sovereignty.
---

# Podcast Creator Agent Skill

Orchestrate podcast/narration audio with Google Cloud TTS Chirp 3 HD on `eu-texttospeech.googleapis.com`. Translation uses `translate-eu.googleapis.com`. Processing stays in the EU.

`synthesizeLongAudio` does not support multi-speaker. Synthesis always uses official `TextToSpeechClient.synthesize_speech` + `multi_speaker_markup` (`gemini-2.5-flash-tts`), batches turns at ≤1500 characters, retries each batch with GAPIC `retry.Retry`, and stitches LINEAR16 WAV locally. Single-speaker scripts inject an unused `Companion` voice (API requires ≥2 speaker definitions). Speaker aliases must be alphanumeric (`[a-zA-Z0-9]+`). Voice names must contain `Chirp3-HD`.

`start_podcast` must return before TTS finishes. It writes `status.json` as `queued` and checks EU `list_voices` first. Poll `get_podcast_status`. There is no download tool.

## Prerequisites

1. APIs enabled: `texttospeech.googleapis.com`, `translate.googleapis.com`, `storage.googleapis.com`
2. `gcloud auth application-default login` and quota project
3. `.env` with `GOOGLE_CLOUD_PROJECT`. MCP also needs `PODCAST_GCS_BUCKET` (EU bucket). CLI uploads are optional.

## CLI workflow

```bash
uv run tts-podcast-creator template --language en-US --output script.yaml
uv run tts-podcast-creator validate --script script.yaml
uv run tts-podcast-creator translate --script script.yaml --to de-DE --output script_de.yaml
uv run tts-podcast-creator synthesize --script script_de.yaml --output output/episode.wav
# optional upload:
uv run tts-podcast-creator synthesize --script script_de.yaml --output output/episode.wav \
  --gcs-uri gs://your-eu-bucket/podcasts/episode_$(date +%s).wav
uv run tts-podcast-creator download --gcs-uri gs://your-eu-bucket/podcasts/episode.wav --output output/episode.wav
```

How to run the tools: [`docs/creating-audio.md`](../../../docs/creating-audio.md). Why these APIs: [`docs/gcp-design.md`](../../../docs/gcp-design.md).

## MCP HTTP workflow

Anonymous Streamable HTTP at `http://127.0.0.1:8000/mcp`. `start_podcast` must return before TTS finishes. Poll `get_podcast_status`. There is no download tool.

```bash
# Terminal 1
uv run tts-podcast-mcp

# Terminal 2 — official Inspector (Node 22.19+)
npx @modelcontextprotocol/inspector --server-url http://127.0.0.1:8000/mcp --transport http
```

Tools: `validate_script`, `start_podcast` (YAML/JSON string payload, optional `translate_to`), `get_podcast_status`, `cancel_podcast` (cooperative between batches). Send the script in the tool arguments, not a `gs://` link. Cap is `PODCAST_MAX_SCRIPT_BYTES` (default 512 KiB).

Cursor/VS Code MCP config: HTTP URL `http://127.0.0.1:8000/mcp`, no auth. Debug with launch config **Podcast Creator: MCP HTTP**.

On success, `audio_uri` is the WAV blob. Download it with the caller's GCS credentials. Multiple jobs run concurrently (`PODCAST_MAX_CONCURRENT_JOBS`, default 4).

Script schema:

- `metadata`: `title`, `language_code`, `audio_encoding` (`LINEAR16`), `sample_rate_hertz` (24000)
- `voices`: alphanumeric aliases → Chirp 3 HD voice names
- `turns`: `speaker`, `text`, optional `pause_after_ms` (silence inserted between batches)

## Troubleshooting

- `502` / `504`: batch over ~1500–3500 characters. Cap is 1500; oversized turns are split.
- `429`: GAPIC retry on the batch RPC handles this.
- `Multi-speaker synthesis requires well structured speaker aliases`: aliases must be `[a-zA-Z0-9]+`.
- `voices` lists Chirp 3 HD via EU `list_voices` (requires ADC).
- Cancel cannot abort an in-flight `synthesize_speech` RPC; it takes effect at the next batch boundary. Restart the MCP process if the whole server is wedged.
- Stale `queued`/`running` after a crash: `get_podcast_status` fails the job after 30 min with no heartbeat. There is no resume — start a new job.
- Non-EU GCS bucket: MCP will not start; `synthesize --gcs-uri` refuses the write. `download` warns and continues.

```bash
uv run tts-podcast-creator voices --language de-DE
uv run tts-podcast-creator --help
```
