---
name: audio-conversation
description: >-
  Create conversation or narration audio (including an audio overview) from a
  YAML/JSON script or a topic using the host's already-configured MCP tools.
  Use when the user wants to create, synthesize, or overview dialogue audio,
  check job progress, cancel a conversation job, validate a script, translate
  then synthesize, or narrate with Chirp 3 HD voices. Requires the
  tts-audio-conversation MCP server to be connected in the harness; never
  starts the MCP process and never collects an MCP URL.
license: Apache-2.0
compatibility: >-
  Any Agent Skills host with native MCP tools. Optimized for Google Antigravity
  (background /tasks). The harness must already expose the six conversation MCP
  tools. Create also needs Application Default Credentials that can read the
  MCP staging bucket, gcloud storage, and a local WAV path. Does not start the
  MCP server.
metadata:
  version: "1.3"
---

# Audio conversation (MCP)

This skill is the playbook for conversation/narration audio. The **harness must already have** the conversation MCP connected. Do not collect an MCP URL. Do not start, spawn, or restart the MCP process.

Create, validate, translate, start, poll, and cancel **only** through that server’s MCP tools. This repo also ships a Python CLI and library — **ignore them**. Never run `uv run tts-audio-conversation`, never call `synthesize` in a shell, never import the Python library.

The server does **not** expose MCP tasks (`tasks=False`). Long-running work is a **job**: `start_conversation` returns immediately; progress is `get_conversation_status`; stop is `cancel_conversation`. Allowed non-MCP shell is **only** `gcloud storage objects describe` (read probe) and `gcloud storage cp` (WAV download after `succeeded`).

## Install

```bash
npx skills add se02035/agentic-audio-conversations
# local checkout:
npx skills add ./skills/audio-conversation
```

Attach MCP in the harness separately (sample `serverUrl` in [references/mcp-tools.md](references/mcp-tools.md)). The GitHub install form works after this catalog is on the repository default branch.

## Goal

For **create / audio overview**: a local WAV at a path the user chose. For **progress** and **cancel**: a job snapshot.

## Step 1 — bind the conversation MCP server

Never collect a URL. Bind to **one already-connected** server using this fingerprint (hosts may prefix tool names; match the suffix):

`upload_script`, `validate_script`, `translate_script`, `start_conversation`, `get_conversation_status`, `cancel_conversation`

1. Collect connected servers that expose **all six** names.
2. **One** match → use it for the whole job (do not mix tools from two servers).
3. **Several** matches → prefer the configured name `tts-audio-conversation`. If that name is absent, ask which **server name** to use (not a URL).
4. **Zero** matches → stop. Tell the user to attach this MCP in the harness. Do not start a process.

Sanity: `start_conversation` must return `job_id`, `status: queued`, and `gs://` URIs. If it does not, treat it as the wrong toolset and stop.

Payload shapes: [references/mcp-tools.md](references/mcp-tools.md). Script schema: [references/script-schema.md](references/script-schema.md). Copy-and-adapt templates: [assets/sample-dialogue.yaml](assets/sample-dialogue.yaml), [assets/sample-narration.yaml](assets/sample-narration.yaml).

## Tool catalog (exactly six)

- `upload_script(script)` → `script_uri`. YAML/JSON body. Not idempotent. Skip only when the user already has `gs://`.
- `validate_script(script_uri)` → `valid`, `error`, and when valid: `title`, `speakers`, `turn_count`, `total_characters`. Schema only (no `list_voices`). If `valid` is false: **stop** — do not start TTS.
- `translate_script(script_uri, target_language)` → `output_uri`. **Only if the user asked.** Later calls use `output_uri`.
- `start_conversation(script_uri)` → `job_id`, `status: queued`, reserved `audio_uri`. **Returns before TTS.** Not idempotent. `gs://` only. WAV is **not** ready.
- `get_conversation_status(job_id)` → `queued` | `running` | `succeeded` | `failed` | `cancelled`. On success, `audio_uri` is the WAV blob.
- `cancel_conversation(job_id)` → job record. Cooperative: the current TTS batch may finish.

No MCP `download` or `list_voices`. No CLI entry points. After a successful GCS probe, the next step is the MCP tool `start_conversation` — not a shell `synthesize`.

## Action: create audio overview

Use only the bound MCP server. Do not skip validate. Do not call `start_conversation` twice for the same request. Do not fall back to the Python CLI or library if MCP tools are present.

1. Ask the **local WAV path** (required).
2. Obtain a script: user file or `gs://`, else **YAML authoring** (below) until `validate_script` returns `valid: true`.
3. `upload_script` unless the user already gave `gs://`. Remember `script_uri`.
4. **GCS read probe** (non-MCP): `gcloud storage objects describe <script_uri>`. If ADC/`gcloud` cannot read the object, **stop** — do not start TTS. Probe success is **not** a signal to run the CLI.
5. `validate_script(script_uri)`. If `valid` is false, repair (authoring loop) — do not start. If true, report title, speakers, turn count, character count.
6. If the user asked to translate: `translate_script` → treat `output_uri` as `script_uri` → GCS probe → `validate_script` again.
7. Call the MCP tool `start_conversation(script_uri)` (not a shell). Immediately tell the user `job_id`, `status: queued`, that synthesis can take minutes, and that the WAV is not ready. Keep the chat usable.
8. **Background wait** (not a chat tight-loop, not a subagent): sleep ~5s, `get_conversation_status(job_id)` on the **same** server, repeat until terminal or ~1800s. Antigravity: `/tasks`. Other hosts: equivalent background shell. The user may ask for progress or cancel on this `job_id` while it runs — do **not** start a second job.
9. Terminal:
    - `succeeded` → `gcloud storage cp <audio_uri> <local.wav>`. Create is done **only** when that file exists.
    - `failed` → report `error`. Do not auto-retry `start_conversation`.
    - `cancelled` → report (current batch may have finished).

After the user confirms a drafted script, also write it next to the WAV (e.g. `episode.yaml` beside `episode.wav`).

## Action: get job progress

1. Bind the conversation MCP (Step 1) if not already bound this session.
2. `job_id` if not in the message and not already known; otherwise ask.
3. **One** `get_conversation_status(job_id)`. Do not upload. Do not start.
4. Report `status`, `updated_at`, and `queued`/`running` (still working), `succeeded` (`audio_uri`; `gcloud storage cp` if the local file is missing), `failed` (`error`), or `cancelled`.

## Action: cancel job

1. Bind the conversation MCP (Step 1) if not already bound this session.
2. `job_id` if unknown; otherwise ask.
3. `cancel_conversation(job_id)`.
4. Report the returned record. Warn that the current TTS batch may still finish. Do not start a new job.

## YAML authoring (no file / no `gs://`)

Ask only for what is missing:

- **Required:** topic (what the overview or conversation is about).
- If unclear: 1 speaker (narration) vs 2 speakers (dialogue). Default: 2 for conversation/overview, 1 for narrate.
- If unclear: language (BCP-47). Default `en-US`.
- If unclear: length. Default short (~6–12 turns), not a 15-minute script.
- Optional: tone, speaker labels (aliases `[a-zA-Z0-9]+`, e.g. `Host` / `Guest`).

Copy a sample, then rewrite topic and turns. Do not invent extra top-level keys.

- Two-speaker conversation/overview → [assets/sample-dialogue.yaml](assets/sample-dialogue.yaml)
- One-speaker narrate → [assets/sample-narration.yaml](assets/sample-narration.yaml)

Top-level keys are **only** `metadata`, `voices`, `turns`:

- `metadata`: `title` (required), `description` (optional), `language_code` (default `en-US`), `audio_encoding` (`LINEAR16`), `sample_rate_hertz` (`24000`)
- `voices`: 1 or 2 alphanumeric aliases → `{name, language_code}`; `name` must contain `Chirp3-HD`
- `turns`: `{speaker, text, pause_after_ms?}`; `speaker` must be a `voices` key; `text` is non-empty spoken sentences (no SSML, markdown, or stage directions)

Generation recipe:

1. Copy the matching sample. Keep encoding and sample rate unless the user asked otherwise.
2. Rewrite `metadata.title`, `description`, and `language_code`.
3. If the language changes, rewrite every `voices.*.name` and `voices.*.language_code` to match `metadata.language_code` (Fenrir / Aoede examples in [references/script-schema.md](references/script-schema.md)).
4. Rewrite `turns` for the topic: alternate `Host` / `Guest` for dialogue; one `Narrator` for narration; 6–12 short turns by default. Do **not** declare a Companion voice.
5. **Confirm the draft** with the user, write it next to the output WAV, then:

   1. `upload_script` → `script_uri`
   2. `validate_script`
   3. If `valid` is false: fix from `error`, **re-upload** (new URI; upload is not idempotent), validate again. Max **3** repairs, then stop and show the last `error`.
   4. If `valid` is true: show the validation summary and continue create at the GCS probe.

If `start_conversation` fails on a missing catalog voice, swap to the other example name for that language, re-upload, validate, then start again.

## Async jobs (not MCP tasks)

`queued` means accepted, not finished. Do not poll `get_conversation_status` in the LLM turn loop. Never re-call `start_conversation` to refresh status.

## Constraints

- Never start the MCP server. Never collect an MCP URL.
- Never mix tools from two MCP servers on one job.
- Never use the Python CLI or library: no `uv run tts-audio-conversation`, no `synthesize` / `upload` / `validate` / `status` shell commands, no Python-library imports. If those commands look convenient because this is the package repo, still do not run them.
- Never `start_conversation` before a successful `validate_script`, on `valid: false`, or without a `gs://` URI.
- Never translate unless asked.
- Never assume the WAV exists when status is `queued` or `running`.
- Speaker aliases alphanumeric; voice names contain `Chirp3-HD`.
- Single-speaker scripts get an unused Companion persona on the server.
- Cancel is cooperative.
- If the six MCP tools are missing, **stop**. Do not substitute the CLI.

## Examples

**Create from YAML**  
User: “Turn `script.yaml` into conversation audio and save `./output/episode.wav`.”  
Agent: bind MCP → `upload_script` → `gcloud storage objects describe` → `validate_script` → MCP `start_conversation` → report `job_id` queued → background poll `get_conversation_status` → `gcloud storage cp` to the WAV. Never `uv run tts-audio-conversation synthesize`.

**Create / audio overview**  
User: “Create an audio overview of this YAML and save `./output/episode.wav`.”  
Agent: same async create playbook. Do not block the chat on TTS. Do not collect a URL.

**Create from a topic**  
User: “Make a 2-minute two-speaker conversation about espresso and save `./output/espresso.wav`.”  
Agent: bind MCP → ask any missing authoring fields → copy `assets/sample-dialogue.yaml` → rewrite title/turns → confirm → write `./output/espresso.yaml` → `upload_script` → `validate_script` (repair until valid) → GCS probe → MCP `start_conversation` → background poll → download. Never the Python CLI.

**MCP tools missing**  
User: “Make a two-speaker WAV from this YAML.”  
Agent: no six-tool server → stop; tell them to attach `tts-audio-conversation` in the harness. Never spawn a server. Never fall back to the Python CLI.

**Validate only**  
User: “Validate `script.yaml` against the audio MCP. Don’t synthesize.”  
Agent: bind MCP → `upload_script` → `validate_script` → stop.

**Job progress**  
User: “What’s the status of conversation job `JOB_ID`?”  
Agent: bind MCP → one `get_conversation_status`.

**Cancel**  
User: “Cancel job `JOB_ID`.”  
Agent: bind MCP → `cancel_conversation` → warn the current batch may finish.

**Translate then create**  
User: “Translate `script.yaml` to `de-DE`, then synthesize to `./output/de.wav`.”  
Agent: bind MCP → upload → probe → validate → `translate_script` → probe + validate `output_uri` → `start_conversation` with the translated URI → wait + `gcloud storage cp`.

**Narration**  
User: “Narrate this YAML (one speaker) to `./output/narration.wav`.”  
Agent: bind MCP → create flow. If drafting, copy `assets/sample-narration.yaml` (Companion is server-side).

**Existing `gs://` script**  
User: “Start audio from `gs://bucket/scripts/foo.yaml` and write `./out.wav`.”  
Agent: bind MCP → skip upload → GCS probe → validate → start → wait.

**Several matching servers**  
Prefer `tts-audio-conversation`. If that name is missing, ask which **server name**.

**No GCS access**  
`gcloud storage objects describe` fails → explain ADC/IAM → **refuse** `start_conversation`.

## Antigravity (preferred)

Use native MCP tools on the bound `tts-audio-conversation` server (not Bash `uv run`). Run wait as a background `/tasks` command, not a subagent. `notify_user` for the WAV path if that tool exists.

**Any other harness:** same playbook; background shell instead of `/tasks`.
