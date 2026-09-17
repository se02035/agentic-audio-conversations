---
name: audio-conversation
description: >-
  Create conversation or narration audio (including an audio overview) from a
  YAML/JSON script or a topic using the host's already-configured MCP tools or
  Gemini Enterprise connector actions. Use when the user wants to create,
  synthesize, or overview dialogue audio, check job progress, cancel a
  conversation job, validate a script, translate then synthesize, or narrate
  with Chirp 3 HD voices. Requires the tts-audio-conversation MCP server to be
  connected in the harness or as a Gemini Enterprise custom MCP connector; never
  starts the MCP process and never collects an MCP URL.
license: Apache-2.0
compatibility: >-
  Any Agent Skills host whose MCP tools or Gemini Enterprise connector actions
  already expose the six conversation tools. Desktop (Antigravity, Cursor):
  gcloud plus a local WAV path. Gemini Enterprise assistant: no local
  filesystem; finish on gs:// audio_uri. Does not start the MCP server.
metadata:
  version: "1.4"
---

# Audio conversation (MCP)

This skill is the playbook for conversation/narration audio. The **harness must already have** the conversation MCP connected (desktop MCP server, or Gemini Enterprise custom MCP connector **actions**). Do not collect an MCP URL. Do not start, spawn, or restart the MCP process.

Create, validate, translate, start, poll, and cancel **only** through that server’s MCP tools (GE: the same six names as connector actions). This repo also ships a Python CLI and library — **ignore them**. Never run `uv run tts-audio-conversation`, never call `synthesize` in a shell, never import the Python library.

The server does **not** expose MCP tasks (`tasks=False`). Long-running work is a **job**: `start_conversation` returns immediately; progress is `get_conversation_status`; stop is `cancel_conversation`.

Host-only wait, probe, and download steps: [references/local-desktop.md](references/local-desktop.md) or [references/gemini-enterprise.md](references/gemini-enterprise.md). Do not apply desktop `gcloud` / local-WAV rules on Gemini Enterprise.

## Install

Desktop (Antigravity, Cursor):

```bash
npx skills add se02035/agentic-audio-conversations
# local checkout:
npx skills add ./skills/audio-conversation
```

Gemini Enterprise assistant: zip this folder with `SKILL.md` at the **zip root** (`SKILL.md`, `references/`, `assets/`) and **Upload skill** in the GE web app. MCP must already be a connected data store with all six actions enabled. Details: [references/gemini-enterprise.md](references/gemini-enterprise.md).

The GitHub `npx` install form works after this catalog is on the repository default branch.

## Goal

For **create / audio overview**: a finished job. Desktop also writes a local WAV ([references/local-desktop.md](references/local-desktop.md)). Gemini Enterprise reports `job_id` and `gs://` `audio_uri` ([references/gemini-enterprise.md](references/gemini-enterprise.md)). For **progress** and **cancel**: a job snapshot.

## Step 1 — bind the conversation MCP server

Never collect a URL. Bind to **one already-connected** server using this fingerprint (hosts may prefix tool names or call them **actions**; match the suffix):

`upload_script`, `validate_script`, `translate_script`, `start_conversation`, `get_conversation_status`, `cancel_conversation`

1. Collect connected servers that expose **all six** names.
2. **One** match → use it for the whole job (do not mix tools from two servers).
3. **Several** matches → prefer the configured name `tts-audio-conversation`. If that name is absent, ask which **server name** to use (not a URL).
4. **Zero** matches → stop. Do not start a process. Tell the user using the matching host file (desktop: attach MCP in the harness; GE: enable the six actions on the existing connector).

Sanity: `start_conversation` must return `job_id`, `status: queued`, and `gs://` URIs. If it does not, treat it as the wrong toolset and stop.

Payload shapes: [references/mcp-tools.md](references/mcp-tools.md). Script schema: [references/script-schema.md](references/script-schema.md). Copy-and-adapt templates: [assets/sample-dialogue.yaml](assets/sample-dialogue.yaml), [assets/sample-narration.yaml](assets/sample-narration.yaml).

## Step 2 — pick the host (once per session)

- **Desktop** (Antigravity, Cursor, similar): a workspace path exists and `gcloud` can run → [references/local-desktop.md](references/local-desktop.md).
- **Gemini Enterprise assistant**: browser chat, MCP already registered as connector actions, no durable local path → [references/gemini-enterprise.md](references/gemini-enterprise.md). Do not invoke a custom GE **agent** in the same prompt as this skill.
- If unsure: asking for `./output/foo.wav` would not make sense → GE path.

## Tool catalog (exactly six)

- `upload_script(script)` → `script_uri`. YAML/JSON body. Not idempotent. Skip only when the user already has `gs://`.
- `validate_script(script_uri)` → `valid`, `error`, and when valid: `title`, `speakers`, `turn_count`, `total_characters`. Schema only (no `list_voices`). If `valid` is false: **stop** — do not start TTS.
- `translate_script(script_uri, target_language)` → `output_uri`. **Only if the user asked.** Later calls use `output_uri`.
- `start_conversation(script_uri)` → `job_id`, `status: queued`, reserved `audio_uri`. **Returns before TTS.** Not idempotent. `gs://` only. WAV is **not** ready.
- `get_conversation_status(job_id)` → `queued` | `running` | `succeeded` | `failed` | `cancelled`. On success, `audio_uri` is the WAV blob.
- `cancel_conversation(job_id)` → job record. Cooperative: the current TTS batch may finish.

No MCP `download` or `list_voices`. No CLI entry points. After validate, the next step is the MCP tool `start_conversation` — not a shell `synthesize`. Desktop-only GCS probe lives in [references/local-desktop.md](references/local-desktop.md).

## Action: create audio overview

Use only the bound MCP server. Do not skip validate. Do not call `start_conversation` twice for the same request. Do not fall back to the Python CLI or library if MCP tools are present.

1. Pick the host (Step 2). Desktop: ask the **local WAV path** (required). GE: do **not** ask for a local path.
2. Obtain a script: user file or `gs://`, else **YAML authoring** (below) until `validate_script` returns `valid: true`.
3. `upload_script` unless the user already gave `gs://`. Remember `script_uri`.
4. **Desktop only:** GCS read probe in [references/local-desktop.md](references/local-desktop.md). **GE:** skip the probe; missing `gcloud` is not a reason to refuse TTS.
5. `validate_script(script_uri)`. If `valid` is false, repair (authoring loop) — do not start. If true, report title, speakers, turn count, character count.
6. If the user asked to translate: `translate_script` → treat `output_uri` as `script_uri` → (desktop: probe again) → `validate_script` again.
7. Call the MCP tool `start_conversation(script_uri)` (not a shell). Immediately tell the user `job_id`, `status: queued`, that synthesis can take minutes, and that the WAV is not ready. Keep the chat usable.
8. **Wait and finish** only as the matching host file says. The user may ask for progress or cancel on this `job_id` while it runs — do **not** start a second job.
    - `failed` → report `error`. Do not auto-retry `start_conversation`.
    - `cancelled` → report (current batch may have finished).

## Action: get job progress

1. Bind the conversation MCP (Step 1) if not already bound this session.
2. `job_id` if not in the message and not already known; otherwise ask.
3. **One** `get_conversation_status(job_id)`. Do not upload. Do not start.
4. Report `status`, `updated_at`, and `queued`/`running` (still working), `succeeded` (`audio_uri`; then the host file’s success step), `failed` (`error`), or `cancelled`.

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
5. **Confirm the draft** with the user, then:

   1. Desktop: write the YAML next to the output WAV path. GE: keep the YAML in chat (no local file required).
   2. `upload_script` → `script_uri`
   3. `validate_script`
   4. If `valid` is false: fix from `error`, **re-upload** (new URI; upload is not idempotent), validate again. Max **3** repairs, then stop and show the last `error`.
   5. If `valid` is true: show the validation summary and continue create (desktop: GCS probe; GE: `start_conversation` after validate).

If `start_conversation` fails on a missing catalog voice, swap to the other example name for that language, re-upload, validate, then start again.

## Async jobs (not MCP tasks)

`queued` means accepted, not finished. Do not poll `get_conversation_status` in the LLM turn loop. Never re-call `start_conversation` to refresh status. Desktop background wait vs GE user-driven status: host files.

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
- Never run desktop `gcloud` / local-WAV steps on Gemini Enterprise. Never skip the desktop GCS probe when the host is desktop.

## Examples

**Create from YAML (desktop)**  
User: “Turn `script.yaml` into conversation audio and save `./output/episode.wav`.”  
Agent: bind MCP → desktop host → `upload_script` → GCS probe → `validate_script` → MCP `start_conversation` → report `job_id` queued → background poll → copy WAV. Never `uv run tts-audio-conversation synthesize`.

**Create / audio overview (Gemini Enterprise)**  
User: “Create an audio overview of this YAML.”  
Agent: bind actions → GE host → upload → validate → `start_conversation` → report `job_id` queued and `gs://` URIs. Do not ask for a local path. Do not run `gcloud`. Do not collect a URL.

**Create from a topic (desktop)**  
User: “Make a 2-minute two-speaker conversation about espresso and save `./output/espresso.wav`.”  
Agent: bind MCP → author from `assets/sample-dialogue.yaml` → confirm → write YAML → `upload_script` → `validate_script` → GCS probe → start → background poll → download. Never the Python CLI.

**MCP tools missing**  
User: “Make a two-speaker WAV from this YAML.”  
Agent: no six-tool server → stop; desktop: attach `tts-audio-conversation` in the harness; GE: enable the six connector actions. Never spawn a server. Never fall back to the Python CLI.

**Validate only**  
User: “Validate `script.yaml` against the audio MCP. Don’t synthesize.”  
Agent: bind MCP → `upload_script` → `validate_script` → stop.

**Job progress**  
User: “What’s the status of conversation job `JOB_ID`?”  
Agent: bind MCP → one `get_conversation_status`.

**Cancel**  
User: “Cancel job `JOB_ID`.”  
Agent: bind MCP → `cancel_conversation` → warn the current batch may finish.

**Translate then create (desktop)**  
User: “Translate `script.yaml` to `de-DE`, then synthesize to `./output/de.wav`.”  
Agent: bind MCP → upload → probe → validate → `translate_script` → probe + validate `output_uri` → `start_conversation` → wait + copy WAV.

**Narration**  
User: “Narrate this YAML (one speaker).”  
Agent: bind MCP → create flow for the current host. If drafting, copy `assets/sample-narration.yaml` (Companion is server-side).

**Existing `gs://` script**  
User: “Start audio from `gs://bucket/scripts/foo.yaml`.”  
Agent: bind MCP → skip upload → (desktop: probe) → validate → start → host wait.

**Several matching servers**  
Prefer `tts-audio-conversation`. If that name is missing, ask which **server name**.
