# Creating audio

How to set up GCP, write a script, and produce a WAV via the CLI or the MCP server.

Agent-oriented workflow: [`.agents/skills/podcast-creator/SKILL.md`](../.agents/skills/podcast-creator/SKILL.md). Why the APIs look this way: [`gcp-design.md`](gcp-design.md). Batching and stitching: [`synthesis.md`](synthesis.md).

## Setup

```bash
gcloud services enable texttospeech.googleapis.com translate.googleapis.com storage.googleapis.com --project <PROJECT>
gcloud auth application-default login
gcloud auth application-default set-quota-project <PROJECT>
cp .env.example .env
uv sync --all-extras
```

Set `GOOGLE_CLOUD_PROJECT` in `.env`. MCP also needs `PODCAST_GCS_BUCKET` on the EU allowlist (`EU`, `EUR4`, `europe-central2`, `europe-north1`, `europe-north2`, `europe-southwest1`, `europe-west1`, `europe-west3`, `europe-west4`, `europe-west8`, `europe-west9`, `europe-west10`, `europe-west12`). Optional live-test URI: `PODCAST_TEST_GCS_URI`. Full knobs: [`.env.example`](../.env.example).

MCP startup and `synthesize --gcs-uri` reject non-EU buckets. `download` warns and still fetches. The MCP server is anonymous; it uses **ADC in the server process** for TTS, Translation, and GCS.

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

`validate` / `validate_script` check schema and size (`PODCAST_MAX_SCRIPT_BYTES`, default 512 KiB) without calling `list_voices`. CLI `synthesize` and MCP `start_podcast` also confirm names against the EU Chirp 3 HD catalog.

Sample long-form scripts live in [`templates/`](../templates/).

## CLI

```bash
uv run tts-podcast-creator template --language en-US --output script.yaml
uv run tts-podcast-creator validate --script script.yaml
uv run tts-podcast-creator voices --language de-DE
uv run tts-podcast-creator translate --script script.yaml --to de-DE --output script_de.yaml

uv run tts-podcast-creator synthesize \
  --script script_de.yaml \
  --output output/episode.wav \
  --gcs-uri gs://your-eu-bucket/podcasts/episode.wav   # optional

uv run tts-podcast-creator download \
  --gcs-uri gs://your-eu-bucket/podcasts/episode.wav \
  --output output/episode.wav
```

`--project` overrides `GOOGLE_CLOUD_PROJECT` / ADC. Alias: `podcast-creator`. `--verbose` enables DEBUG logs and OpenTelemetry console spans on stderr. Module form: `python -m tts_podcast_creator.cli`.

## MCP HTTP server

Streamable HTTP only (no stdio), single process, no MCP authentication. `MCP_HOST` must be a loopback address (`127.0.0.1`, `::1`, or `localhost`). Long synthesis is an **app-level job**: `start_podcast` returns immediately with a `job_id` and `gs://` URIs after writing `status.json` as `queued` and checking EU `list_voices`. Poll `get_podcast_status`. There is **no download tool** — fetch the WAV from GCS with your own credentials.

Required env: `GOOGLE_CLOUD_PROJECT`, `PODCAST_GCS_BUCKET`.

```bash
# Terminal 1
uv run tts-podcast-mcp
# or: uv run python -m tts_podcast_creator.mcp
# http://127.0.0.1:8000/mcp (MCP_HOST / MCP_PORT)

# Terminal 2 — Inspector (Node 22.19+)
npx @modelcontextprotocol/inspector --server-url http://127.0.0.1:8000/mcp --transport http
```

VS Code: launch **Podcast Creator: MCP HTTP** (`.vscode/launch.json`, loads `.env`).

Cursor / VS Code MCP config:

```json
{
  "mcpServers": {
    "tts-podcast-creator": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

| Tool | Behavior |
| --- | --- |
| `validate_script` | Schema + size cap. No GCS, no `list_voices`. |
| `start_podcast` | Catalog check, persist `queued`, return. Optional `translate_to` runs in the worker. Send YAML/JSON in the argument, not a `gs://` link. |
| `get_podcast_status` | Memory, else GCS `status.json`. Stale `queued`/`running` (no heartbeat for 30 min) is `failed`. |
| `cancel_podcast` | Cooperative cancel between TTS batches. After restart, writes `cancelled` in GCS without starting a worker. In-flight RPC (~25–30s) may finish; WAV is not kept. |

Jobs run concurrently (`PODCAST_MAX_CONCURRENT_JOBS`, default 4). Extra jobs stay `queued`. One Uvicorn worker — do not scale the process horizontally.

Protocol era in Inspector can stay **legacy** (this server does not use FastMCP native tasks).

## Troubleshooting

- `502` / `504`: batch over ~1500–3500 characters. Cap is 1500; oversized turns are split. See [`synthesis.md`](synthesis.md).
- `429`: GAPIC retry on the batch RPC.
- `Multi-speaker synthesis requires well structured speaker aliases`: aliases must be `[a-zA-Z0-9]+`.
- Voice not in catalog: `synthesize` / `start_podcast` fail before TTS. List voices: `uv run tts-podcast-creator voices --language de-DE`.
- Non-EU bucket: MCP will not listen; `synthesize --gcs-uri` refuses. `download` warns and continues.
- Stale `queued`/`running` after a crash: treated as failed after 30 min. No TTS resume — start a new job.
