# GCP design choices

Why this package pins specific Google Cloud APIs and regions instead of the defaults.

## EU data residency

The product claim is that **speech and translation processing stay in the EU**. Global Text-to-Speech (`texttospeech.googleapis.com`) and global Translation do not satisfy that.

| Concern | Choice | Not used |
| --- | --- | --- |
| Chirp 3 HD synthesis | `eu-texttospeech.googleapis.com` | Global or US TTS endpoints |
| Dialogue translation | `translate-eu.googleapis.com`, location `europe-west1` | Global Translation, `us-central1` |
| Audio at rest | EU-located GCS bucket you configure in GCP (library does not enforce residency on write) | Non-EU buckets (operational choice; AI RPCs still use EU endpoints) |

Writes are enforced in code (`require_eu_bucket` / `require_eu_gcs_uri`) on MCP startup and CLI `synthesize --gcs-uri`. `download` only warns: reading an old non-EU object must not block recovery. Clients are always constructed with explicit project + ADC — never a bare `storage.Client()`.

Agent invariants: [`.agents/rules/gcp-eu-sovereignty.md`](../.agents/rules/gcp-eu-sovereignty.md).

## Multi-speaker vs long audio

Cloud TTS offers two synthesis RPCs:

| API | Long form | Multi-speaker Chirp 3 HD |
| --- | --- | --- |
| `synthesizeLongAudio` | Yes (async, GCS output) | **No** |
| `synthesize_speech` + `multi_speaker_markup` | Short payloads | **Yes** (`gemini-2.5-flash-tts`) |

A two-host podcast needs the second row. Long episodes are handled in-process: batch ≤1500 characters, GAPIC retry per RPC, stitch LINEAR16 locally. See [`synthesis.md`](synthesis.md).

Retry is **per batch**, not around the whole job. A custom outer loop would re-bill completed batches on a late 429.

## Translation

Cloud Translation v3 `translate_text` is used with parent `projects/{id}/locations/europe-west1`. One RPC is enough for short scripts; long templates can exceed a comfortable payload, so contents are packed at **8000 characters** per request (under the 102400 code-point API cap).

Voice remapping is string-level locale prefix swap, then a catalog check on `synthesize` / `start_conversation` so a missing German Chirp 3 persona fails before billed TTS.

## MCP jobs vs FastMCP native tasks

Synthesis can take minutes. MCP tools must not block that long.

This server uses **app-level jobs** (`JobManager`): `start_conversation` returns `job_id` + `gs://` URIs after `list_voices` and persisting `queued`. FastMCP native tasks are off (`tasks=False`). Inspector protocol era can stay legacy.

Status lives in memory for the live process and in GCS `status.json` so polls survive a restart. There is **no TTS resume** and **no stale auto-fail**. Orphaned `queued`/`running` rows remain until cancelled or a new job is started. Cancel after restart only writes `cancelled` — the worker is gone.

Audio is never streamed back through MCP. Clients download the WAV with their own GCS credentials. That keeps the anonymous localhost server from becoming a file proxy and keeps the large blob on the EU bucket.

Concurrency is capped (`AUDIO_CONVERSATION_MAX_CONCURRENT_JOBS`, default 4) to protect Cloud TTS quota. The process is **single-worker**; horizontal replicas would split in-memory jobs.

## Observability

OpenTelemetry spans (`synthesize_script`, `tts.batch`, `conversation.job`, GCS upload) export to stderr and optionally Cloud Trace (`OTEL_TRACES_EXPORTER`). Console spans stay on stderr so they do not corrupt HTTP JSON-RPC.
