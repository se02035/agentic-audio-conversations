# AGENTS.md

Orientation for agents changing this Python package. To *create audio*, follow [`.agents/skills/audio-conversation/SKILL.md`](.agents/skills/audio-conversation/SKILL.md) and [`docs/creating-audio.md`](docs/creating-audio.md). Library facade: [`docs/library-api.md`](docs/library-api.md). Design rationale: [`docs/gcp-design.md`](docs/gcp-design.md). Synthesis batching: [`docs/synthesis.md`](docs/synthesis.md). Overview: [`README.md`](README.md).

## Stack

Hatchling **src layout**, Python **>=3.11**, package `tts_audio_conversation`. Install with `uv sync --extra dev` (or `--all-extras`). Copy [`.env.example`](.env.example) to `.env` for GCP. Shared synthesis lives in `logic/`; CLI and MCP are thin adapters over `AudioConversationService`.

## Where to change what

- [`src/tts_audio_conversation/logic/service.py`](src/tts_audio_conversation/logic/service.py) — public facade + factory
- [`src/tts_audio_conversation/logic/models.py`](src/tts_audio_conversation/logic/models.py) — YAML/JSON script schema (`ConversationScript`)
- [`src/tts_audio_conversation/logic/jobs/`](src/tts_audio_conversation/logic/jobs/) — table-driven FSM, `ConversationCreationJob`, `JobManager`
- [`src/tts_audio_conversation/logic/pipeline.py`](src/tts_audio_conversation/logic/pipeline.py) — voices → TTS → upload
- [`src/tts_audio_conversation/logic/tts.py`](src/tts_audio_conversation/logic/tts.py) / [`batching.py`](src/tts_audio_conversation/logic/batching.py) — EU TTS + turn packing (`MAX_BATCH_CHARS=1500`)
- [`src/tts_audio_conversation/logic/translator.py`](src/tts_audio_conversation/logic/translator.py) — Cloud Translation (`translate-eu`, `europe-west1`)
- [`src/tts_audio_conversation/logic/storage.py`](src/tts_audio_conversation/logic/storage.py) — GCS upload/download/delete (no residency checks)
- [`src/tts_audio_conversation/logic/settings.py`](src/tts_audio_conversation/logic/settings.py) — env (`GOOGLE_CLOUD_PROJECT`, `AUDIO_CONVERSATION_*`, MCP bind, OTEL)
- [`src/tts_audio_conversation/cli/`](src/tts_audio_conversation/cli/) — Click adapter (`uv run tts-audio-conversation`)
- [`src/tts_audio_conversation/mcp/server.py`](src/tts_audio_conversation/mcp/server.py) — FastMCP HTTP tools at `/mcp`
- [`templates/`](templates/) — long-form sample scripts (slow live tests)
- [`tests/logic/`](tests/logic/), [`tests/cli/`](tests/cli/), [`tests/mcp/`](tests/mcp/) — **unit** (mocked GCP)
- [`tests/integration/`](tests/integration/) — **live** EU GCP
- Config: [`pyproject.toml`](pyproject.toml), [`.github/workflows/ci.yml`](.github/workflows/ci.yml)

```mermaid
flowchart LR
  script[YAML_or_JSON]
  svc[AudioConversationService]
  cli[CLI]
  mcp[MCP_HTTP]
  tts[EU_TTS]
  gcs[GCS]
  script --> cli
  script --> mcp
  cli --> svc
  mcp --> svc
  svc --> tts
  svc --> gcs
```

## Quality gate (match CI)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest -m unit --cov=src/tts_audio_conversation --cov-fail-under=85
```

Format in place with `uv run ruff format .`. Optional: `uv run pre-commit run --all-files`.

Ruff: line length 100, Google pydocstyle; ignores `D100`/`D104`/`D107`. Mypy is `strict`.

## Tests

Markers in [`pyproject.toml`](pyproject.toml); auto-applied in [`tests/conftest.py`](tests/conftest.py): paths under `tests/integration/` get `integration`; everything else gets `unit`. Nodeids matching unicorn / German AI / `test_mcp_long` also get `slow`.

| Intent | Command | When |
| --- | --- | --- |
| Default / CI | `uv run pytest -m unit --cov=… --cov-fail-under=85` | Always after code changes |
| Live EU smoke | `uv run pytest -m "integration and not slow"` | ADC + `.env`; billed GCP |
| Long-form (~15 min each) | `uv run pytest -m slow` | Only if the user asks |

Live tests skip unless `GOOGLE_CLOUD_PROJECT` and `AUDIO_CONVERSATION_TEST_GCS_URI` / staging bucket are set. Delete GCS blobs in `finally` unless `KEEP_TEST_ARTIFACTS=true`. Do not commit `*.wav`, `*.mp3`, or `.env`.

## Invariants

- TTS only via `eu-texttospeech.googleapis.com`; Translation only via `translate-eu.googleapis.com` / `europe-west1`. Callers own EU bucket choice (no write-time residency checks).
- Official `TextToSpeechClient.synthesize_speech` + `multi_speaker_markup`. Never `synthesizeLongAudio`.
- Speaker aliases `[a-zA-Z0-9]+`. Voice names must contain `Chirp3-HD`. `list_voices` on synthesize/start only.
- One-speaker scripts get an unused Companion persona.
- Batch turns at ≤1500 characters. Retry each `synthesize_speech` with GAPIC `retry.Retry`. Translation batches at 8000 characters per RPC.
- Public processing after upload takes `gs://` script URIs only. Audio creation is async-job only.
- MCP `start_conversation` may write `status.json` (`queued`) and call `list_voices`, but must return before `synthesize_speech`. Single Uvicorn worker.
- No stale auto-fail / no TTS resume. Cancel after restart writes GCS `cancelled` only.
- Project flag is `--project` (not `--project-id`).
- Adapters must not call `batch_turns`, `assert_voices_*`, translator, or TTS directly — only `AudioConversationService`.
