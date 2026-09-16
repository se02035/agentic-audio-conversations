# AGENTS.md

Orientation for agents changing this Python package. To *create audio*, follow [`.agents/skills/podcast-creator/SKILL.md`](.agents/skills/podcast-creator/SKILL.md) and [`docs/creating-audio.md`](docs/creating-audio.md). Product invariants: [`.agents/rules/gcp-eu-sovereignty.md`](.agents/rules/gcp-eu-sovereignty.md). Design rationale: [`docs/gcp-design.md`](docs/gcp-design.md). Synthesis batching: [`docs/synthesis.md`](docs/synthesis.md). Overview: [`README.md`](README.md).

## Stack

Hatchling **src layout**, Python **>=3.11**, package `tts_podcast_creator`. Install with `uv sync --extra dev` (or `--all-extras`). Copy [`.env.example`](.env.example) to `.env` for GCP. Shared synthesis lives in `logic/`; CLI and MCP wrap it.

## Where to change what

- [`src/tts_podcast_creator/logic/models.py`](src/tts_podcast_creator/logic/models.py) — YAML/JSON script schema (`PodcastScript`)
- [`src/tts_podcast_creator/logic/client.py`](src/tts_podcast_creator/logic/client.py) — EU TTS (`EU_TTS_ENDPOINT`, `MAX_BATCH_CHARS=1500`)
- [`src/tts_podcast_creator/logic/translator.py`](src/tts_podcast_creator/logic/translator.py) — Cloud Translation (`translate-eu`, `europe-west1`)
- [`src/tts_podcast_creator/logic/storage.py`](src/tts_podcast_creator/logic/storage.py) — GCS upload/download/delete
- [`src/tts_podcast_creator/logic/auth.py`](src/tts_podcast_creator/logic/auth.py) — ADC + project resolution
- [`src/tts_podcast_creator/logic/settings.py`](src/tts_podcast_creator/logic/settings.py) — env (`GOOGLE_CLOUD_PROJECT`, `PODCAST_GCS_BUCKET`, MCP bind, OTEL)
- [`src/tts_podcast_creator/cli/`](src/tts_podcast_creator/cli/) — Click: `template`, `validate`, `voices`, `translate`, `synthesize`, `download` (`uv run tts-podcast-creator`; alias `podcast-creator`)
- [`src/tts_podcast_creator/mcp/server.py`](src/tts_podcast_creator/mcp/server.py) — FastMCP HTTP tools at `/mcp`
- [`src/tts_podcast_creator/mcp/jobs.py`](src/tts_podcast_creator/mcp/jobs.py) — background jobs, cancel, concurrency
- [`templates/`](templates/) — long-form sample scripts (slow live tests)
- [`tests/logic/`](tests/logic/), [`tests/cli/`](tests/cli/), [`tests/mcp/`](tests/mcp/) — **unit** (mocked GCP)
- [`tests/integration/`](tests/integration/) — **live** EU GCP
- [`docs/creating-audio.md`](docs/creating-audio.md) — setup, CLI, MCP
- [`docs/synthesis.md`](docs/synthesis.md) — batching and WAV stitch
- [`docs/gcp-design.md`](docs/gcp-design.md) — why these GCP APIs
- Config: [`pyproject.toml`](pyproject.toml), [`.github/workflows/ci.yml`](.github/workflows/ci.yml)

```mermaid
flowchart LR
  script[YAML_or_JSON_script]
  logic[logic_package]
  cli[CLI]
  mcp[MCP_HTTP]
  tts[EU_TTS]
  gcs[EU_GCS]
  script --> cli
  script --> mcp
  cli --> logic
  mcp --> logic
  logic --> tts
  logic --> gcs
```

## Quality gate (match CI)

After code changes, run the same commands as [`.github/workflows/ci.yml`](.github/workflows/ci.yml):

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest -m unit
```

Format in place with `uv run ruff format .`. Optional: `uv run pre-commit run --all-files`.

Ruff: line length 100, Google pydocstyle; ignores `D100`/`D104`/`D107`. Mypy is `strict`. VS Code formats with Ruff and runs pytest `-m unit`.

## Tests

Markers are declared in [`pyproject.toml`](pyproject.toml) and auto-applied in [`tests/conftest.py`](tests/conftest.py): paths under `tests/integration/` get `integration`; everything else gets `unit`. Nodeids matching unicorn / German AI / `test_mcp_long` also get `slow`.

| Intent | Command | When |
| --- | --- | --- |
| Default / CI | `uv run pytest -m unit` | Always after code changes |
| Live EU smoke | `uv run pytest -m "integration and not slow"` | ADC + `.env`; billed GCP |
| Long-form (~15 min each) | `uv run pytest -m slow` | Only if the user asks |

Live tests skip unless `GOOGLE_CLOUD_PROJECT` and `PODCAST_TEST_GCS_URI` are set (MCP live tests also use `PODCAST_GCS_BUCKET`). Delete GCS blobs in `finally` unless `KEEP_TEST_ARTIFACTS=true`. Do not commit `*.wav`, `*.mp3`, or `.env`.

## Invariants

Full list: [`.agents/rules/gcp-eu-sovereignty.md`](.agents/rules/gcp-eu-sovereignty.md).

- TTS only via `eu-texttospeech.googleapis.com`; Translation only via `translate-eu.googleapis.com` / `europe-west1`; GCS buckets on the EU allowlist (`EU`, `EUR4`, and the listed `europe-*` regions in `is_eu_bucket_location`; enforced on writes).
- Official `TextToSpeechClient.synthesize_speech` + `multi_speaker_markup`. Never `synthesizeLongAudio`.
- Speaker aliases `[a-zA-Z0-9]+`. Voice names must contain `Chirp3-HD`. `list_voices` on synthesize/start_podcast only.
- One-speaker scripts get an unused Companion persona.
- Batch turns at ≤1500 characters. Retry each `synthesize_speech` with GAPIC `retry.Retry`. Translation batches at 8000 characters per RPC.
- MCP `start_podcast` may write `status.json` (`queued`) and call `list_voices`, but must return before `synthesize_speech`. Single Uvicorn worker; do not scale the process horizontally.
- Stale `queued`/`running` jobs (30 min, no heartbeat) become `failed`; no TTS resume. Cancel after restart writes GCS `cancelled` only.
- Project flag is `--project` (not `--project-id`).
