# MCP tool payloads

The harness must already connect this server. The skill never collects a URL. FastMCP `tasks=False`: there is no MCP task API. Long-running work is a **job** (`job_id` + `get_conversation_status`).

Bind the server whose tools include all six names below (prefer configured name `tts-audio-conversation`). Hosts may prefix tool names or expose them as connector actions; match the suffix. Call order lives in `SKILL.md`.

Load this file when a payload is unexpected.

## `upload_script`

Args: `{ "script": "<yaml or json string>" }`

Result:

```json
{ "script_uri": "gs://bucket/conversation/scripts/<id>/script.yaml", "script_id": "<uuid>" }
```

Not idempotent. Skip when the user already has a `gs://` script URI.

## `validate_script`

Args: `{ "script_uri": "gs://..." }`

Valid:

```json
{
  "valid": true,
  "title": "Tech Pulse Europe",
  "language_code": "en-US",
  "speakers": ["Host", "Guest"],
  "turn_count": 8,
  "total_characters": 1200,
  "script_uri": "gs://...",
  "error": null
}
```

Invalid: `valid` is false and `error` explains the schema problem. Do not call `start_conversation`. Schema only — does not call `list_voices`.

## `translate_script`

Only when the user asked for another language.

Args: `{ "script_uri": "gs://...", "target_language": "de-DE" }`

Result: `{ "output_uri": "gs://...", "language_code": "de-DE", "title": "...", "skipped": false }`

Later tools use `output_uri`, not the original URI.

## `start_conversation`

Args: `{ "script_uri": "gs://..." }` — GCS URI only.

Result (`JobRecord`), **before any TTS**:

```json
{
  "job_id": "<uuid>",
  "status": "queued",
  "script_uri": "gs://...",
  "audio_uri": "gs://bucket/conversation/jobs/<id>/output/audio.wav",
  "status_uri": "gs://bucket/conversation/jobs/<id>/status.json",
  "error": null,
  "updated_at": "2026-01-01T00:00:00Z"
}
```

`audio_uri` is reserved; the WAV is not ready while `status` is `queued` or `running`. Not idempotent. Do not call again to refresh status. If the result is not `job_id` + `queued` + `gs://` URIs, this is the wrong MCP server.

May fail if a voice name is absent from the EU Chirp 3 HD catalog (`list_voices` runs here, not in `validate_script`).

## `get_conversation_status`

Args: `{ "job_id": "<uuid>" }`

Same `JobRecord`. `status` is `queued` | `running` | `succeeded` | `failed` | `cancelled`. On `failed`, read `error`. On `succeeded`, `audio_uri` is the WAV blob. Unknown `job_id` is a tool error.

## `cancel_conversation`

Args: `{ "job_id": "<uuid>" }`

Returns a `JobRecord`. Cooperative: the current TTS batch may finish; later batches are skipped.

## No download tool

Create/start/status go through MCP tools (or Gemini Enterprise connector actions) in the harness, not the Python CLI. There is no MCP `download` tool.

Desktop copy of `audio_uri` with ADC: [local-desktop.md](local-desktop.md). Gemini Enterprise reports the `gs://` URI: [gemini-enterprise.md](gemini-enterprise.md). Never start the MCP process.
