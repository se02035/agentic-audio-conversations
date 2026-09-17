# Desktop host (Antigravity, Cursor)

Use this file when Step 2 in `SKILL.md` picked **desktop**: a workspace path exists and `gcloud` can run. Do not use these steps in the Gemini Enterprise assistant.

Create, validate, start, and cancel still go through the bound MCP tools in `SKILL.md`. Allowed non-MCP shell is **only** `gcloud storage objects describe` (read probe) and `gcloud storage cp` (WAV download after `succeeded`).

## Local WAV path

Ask for the **local WAV path** (required) before upload. Create is done **only** when that file exists.

After the user confirms a drafted script, also write it next to the WAV (e.g. `episode.yaml` beside `episode.wav`).

## GCS read probe

Before `start_conversation` (and again after `translate_script` on the `output_uri`):

```bash
gcloud storage objects describe <script_uri>
```

If ADC/`gcloud` cannot read the object, **stop** — do not start TTS. Explain ADC/IAM. Probe success is **not** a signal to run the Python CLI.

## Background wait

Not a chat tight-loop, not a subagent. Sleep ~5s, `get_conversation_status(job_id)` on the **same** server, repeat until terminal or ~1800s.

- Antigravity: `/tasks`. `notify_user` for the WAV path if that tool exists.
- Cursor and other desktop hosts: equivalent background shell.

The user may ask for progress or cancel on this `job_id` while it runs — do **not** start a second job.

## Success

On `succeeded`:

```bash
gcloud storage cp <audio_uri> <local.wav>
```

Create is done **only** when that file exists. On later progress checks, run `gcloud storage cp` if the local file is missing.

On `get_conversation_status` while `queued`/`running`: still working. Never assume the WAV exists yet.

## Operator MCP wiring (not this skill)

Operators attach HTTP MCP themselves. Use **`serverUrl`** (not `url` / `httpUrl`). Preferred server key: `tts-audio-conversation`.

```json
{
  "mcpServers": {
    "tts-audio-conversation": {
      "serverUrl": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

Do not write this file from the skill. Never start the MCP process. Never collect an MCP URL.
