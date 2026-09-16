# Creating audio

How to set up GCP, write a script, and produce a WAV via the CLI, the MCP server, or the ADK Web agent.

Agent-oriented workflow: [`.agents/skills/audio-conversation/SKILL.md`](../.agents/skills/audio-conversation/SKILL.md). Library facade: [`library-api.md`](library-api.md). Why the APIs look this way: [`gcp-design.md`](gcp-design.md). Batching and stitching: [`synthesis.md`](synthesis.md).

## Setup

```bash
gcloud services enable texttospeech.googleapis.com translate.googleapis.com storage.googleapis.com --project <PROJECT>
gcloud auth application-default login
gcloud auth application-default set-quota-project <PROJECT>
cp .env.example .env
uv sync --all-extras
```

Set `GOOGLE_CLOUD_PROJECT` in `.env`. Staging needs `AUDIO_CONVERSATION_GCS_STAGING_BUCKET` (choose an EU bucket in GCP; the library does not enforce residency on writes). Optional live-test URI: `AUDIO_CONVERSATION_TEST_GCS_URI`. Full knobs: [`.env.example`](../.env.example).

The MCP server is anonymous; it uses **ADC in the server process** for TTS, Translation, and GCS.

## Script schema

YAML or JSON. Voice names must contain `Chirp3-HD`. Speaker aliases are alphanumeric (`[a-zA-Z0-9]+`).

```yaml
metadata:
  title: Tech Pulse Europe
  language_code: en-US
  audio_encoding: LINEAR16
  sample_rate_hertz: 24000
voices:
  host:
    name: en-US-Chirp3-HD-Fenrir
    language_code: en-US
  guest:
    name: en-US-Chirp3-HD-Aoede
    language_code: en-US
turns:
  - speaker: host
    text: Welcome to the show.
    pause_after_ms: 300
  - speaker: guest
    text: Glad to be here.
```

`validate` / `validate_script` check schema and size (`AUDIO_CONVERSATION_MAX_SCRIPT_BYTES`, default 512 KiB) without calling `list_voices`. `create_audio` / `start_conversation` also confirm names against the EU Chirp 3 HD catalog.

Sample long-form scripts live in [`templates/`](../templates/).

## Shared flow (CLI and MCP)

```text
upload_script → [validate_script] → [translate_script] → create_audio / start_conversation
```

Local or inline input is only accepted by `upload_script`. Everything else takes a `gs://` script URI.

## CLI

```bash
uv run tts-audio-conversation template --language en-US --output script.yaml
uv run tts-audio-conversation upload --script script.yaml
# prints script_uri / script_id

uv run tts-audio-conversation validate --script-uri gs://…/scripts/{id}/script.yaml
uv run tts-audio-conversation voices --language de-DE
uv run tts-audio-conversation translate --script-uri gs://…/script.yaml --to de-DE
# prints output_uri

uv run tts-audio-conversation synthesize --script-uri gs://…/script.de-DE.yaml --output output/episode.wav
uv run tts-audio-conversation status --job-id <uuid>
uv run tts-audio-conversation download --gcs-uri gs://…/jobs/{id}/output/audio.wav --output output/episode.wav
```

`synthesize` starts a background job, polls until terminal, and optionally downloads the WAV. `--project` overrides `GOOGLE_CLOUD_PROJECT` / ADC. Alias: `audio-conversation`. `--verbose` enables DEBUG logs and OpenTelemetry console spans on stderr.

## MCP HTTP server

Streamable HTTP only (no stdio), single process, no MCP authentication. `MCP_HOST` must be a loopback address (`127.0.0.1`, `::1`, or `localhost`). `start_conversation` returns immediately with a `job_id` and `gs://` URIs after writing `status.json` as `queued` and checking EU `list_voices`. Poll `get_conversation_status`. There is **no download tool** — fetch the WAV from GCS with your own credentials. The server advertises MCP `instructions` (workflow) and tool annotations (`readOnlyHint` on `validate_script` / `get_conversation_status` so clients can skip confirmation on those reads).

Required env: `GOOGLE_CLOUD_PROJECT`, `AUDIO_CONVERSATION_GCS_STAGING_BUCKET`.

```bash
uv run tts-audio-conversation-mcp
# http://127.0.0.1:8000/mcp
```

| Tool | Behavior |
| --- | --- |
| `upload_script` | Inline YAML/JSON → `…/scripts/{id}/script.yaml`; returns `script_uri` |
| `validate_script` | Soft-validate a `gs://` URI (no `list_voices`) |
| `translate_script` | Translate via translate-eu; write sibling; return `output_uri` |
| `start_conversation` | `script_uri` only; catalog check, persist `queued`, return |
| `get_conversation_status` | Memory, else GCS `status.json` (no stale auto-fail) |
| `cancel_conversation` | Cooperative cancel between TTS batches |

Jobs run concurrently (`AUDIO_CONVERSATION_MAX_CONCURRENT_JOBS`, default 4). Extra jobs stay `queued`. One Uvicorn worker — do not scale the process horizontally.

## ADK Web agent

Optional adapter: a Google ADK `LlmAgent` that talks to MCP HTTP (not TTS directly). Install the extra, enable Vertex/Gemini Enterprise (`aiplatform.googleapis.com`), and keep Gemini on the **global** location. Speech/translation stay on EU endpoints in the MCP process.

```bash
uv sync --extra adk --extra dev
# .env: GOOGLE_GENAI_USE_ENTERPRISE=true, GOOGLE_CLOUD_LOCATION=global,
# ADK_AGENT_MODEL=gemini-3.8-flash, AUDIO_CONVERSATION_MCP_URL=http://127.0.0.1:8000/mcp
uv run tts-audio-conversation-mcp
uv run adk web --host 127.0.0.1 --port 8080 --no-reload src/tts_audio_conversation/adk
```

VS Code: compound **Audio Overview: MCP + ADK Web** (MCP on 8000, ADK Web on 8080). Upload the short [`src/tts_audio_conversation/adk/example_script.yaml`](../src/tts_audio_conversation/adk/example_script.yaml) (not the long [`templates/`](../templates/) scripts).

`create_audio_conversation` is a `LongRunningFunctionTool`: it only calls MCP `start_conversation` and returns `{job_id, status: queued}`. The playground HTTP request does **not** wait on TTS. An App plugin polls `get_conversation_status` off-request, then resumes with a `FunctionResponse` whose id matches the original function call. On success it downloads the WAV and saves unique artifacts `script_{script_id}.yaml` and `audio_{job_id}_{language}.wav`. ADK Web's Artifacts tab lists files from session `artifactDelta` events (the same path as YAML uploads), not from `save_artifact` alone.

Automated ADK tests that need the runtime spawn the real [`adk api_server`](https://adk.dev/runtime/api-server/) CLI (not `adk web`, not an in-process fake) against `src/tts_audio_conversation/adk` and drive `/list-apps`, session CRUD, `POST /run`, and artifacts over HTTP. Those tests live under `tests/integration/adk/` even when MCP TTS/GCS are mocked. Unit tests under `tests/unit/adk/` cover ingest/create tools, the LRO plugin, and the MCP client without starting the API server. Gemini is the configured live model on the Gemini LRO integration test.

## Live and slow tests

Short live suites (ADC + EU bucket in `.env`):

```bash
uv run pytest -m "integration and not slow"
uv run pytest -m "e2e and not slow"
```

Long-form `slow` tests (unicorn fairytale + German AI software engineering, each via library and MCP) synthesize full [`templates/`](../templates/) scripts. There are four tests; each alone can take ~8–15 minutes of billed TTS.

**Always parallelize** with pytest-xdist so wall-clock stays near one job instead of four sequential:

```bash
uv run pytest -m slow -n 4
```

Use `-n 4` (one worker per test), not bare `pytest -m slow`. Drop to `-n 2` if EU TTS quota throttles. Tests isolate GCS prefixes and MCP ports, so xdist is safe. Agent-oriented marker table: [`AGENTS.md`](../AGENTS.md).

## Troubleshooting

- `502` / `504`: batch over ~1500–3500 characters. Cap is 1500; oversized turns are split. See [`synthesis.md`](synthesis.md).
- `429`: GAPIC retry on the batch RPC.
- `Multi-speaker synthesis requires well structured speaker aliases`: aliases must be `[a-zA-Z0-9]+`.
- Voice not in catalog: `create_audio` / `start_conversation` fail before TTS. List voices: `uv run tts-audio-conversation voices --language de-DE`.
- After a process crash, orphaned `queued`/`running` status.json remains as-is; cancel writes `cancelled` without starting a worker. No TTS resume — start a new job.
