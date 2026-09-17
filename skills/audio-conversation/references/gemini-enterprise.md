# Gemini Enterprise assistant

Use this file when Step 2 in `SKILL.md` picked **Gemini Enterprise assistant**: browser chat, MCP already registered as a custom MCP connector, no durable local path. Skills apply to the **assistant**, not Workflow Builder / Agent Designer agents. Do not invoke a custom GE **agent** and this skill in the same prompt.

Do **not** use [local-desktop.md](local-desktop.md) on this host.

## Connector actions

The six conversation tools appear as connector **actions**. Names may be prefixed; match the suffix fingerprint in `SKILL.md`. Prefer the connector whose tools include all six.

**Zero matches:** stop. Tell the user to **enable all six actions** on the existing custom MCP data store. Do not paste an MCP URL. Do not start, spawn, or restart the MCP process.

Mutating actions typically need user confirmation: `upload_script`, `translate_script`, `start_conversation`, `cancel_conversation`. `validate_script` and `get_conversation_status` are read-only.

## No local filesystem

- Do **not** ask for a local WAV path.
- Do **not** write YAML next to a WAV. Keep a confirmed draft in the chat (or ask the user to paste/upload it).
- Do **not** run `gcloud` (`storage objects describe`, `storage cp`, or anything else). Missing `gcloud` is **not** a reason to refuse `start_conversation`.

## Wait

After `start_conversation` returns `queued`, keep the chat usable. Do **not** emulate Antigravity `/tasks`. Do not poll `get_conversation_status` in the LLM turn loop.

Tell the user synthesis can take minutes and that they can ask for progress (or `@` / `/` this skill again) with the `job_id`. When they ask: **one** `get_conversation_status(job_id)`. Do not start a second job.

## Success

Done = `status: succeeded`. Report `job_id` and `audio_uri` (`gs://`). The WAV lives in the MCP staging bucket. This host cannot copy it to the user’s laptop; they fetch it outside the GE app (console, `gcloud` on their machine, and so on).

On `failed`, report `error`. Do not auto-retry `start_conversation`. On `cancelled`, report (the current TTS batch may have finished).

## Upload this skill

Zip **from inside** `skills/audio-conversation` so `SKILL.md` is at the zip root:

```bash
cd skills/audio-conversation
zip -r ../../audio-conversation.zip SKILL.md references assets
```

Then **Upload skill** in the Gemini Enterprise web app (Skills). Include `references/` and `assets/`. Do not zip `.agents/skills/audio-conversation`. Skills must be enabled on the app (Feature Management). Frontline cannot upload custom skills.
