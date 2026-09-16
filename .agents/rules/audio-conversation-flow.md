---
trigger: always_on
description: >-
  URI-only processing after upload; CLI and MCP share the same facade flow.
---

# Why this rule exists

Agents keep re-introducing inline-script start/translate or embedding translation inside synthesis. Keep the PoC contract explicit.

# Audio conversation flow

1. `upload_script` accepts local `Path` or inline YAML/JSON only.
2. After upload, every processing call takes a `gs://` script URI: `validate_script`, `translate_script`, `create_audio` / `start_conversation`.
3. Translation is a separate facade step. Never translate inside `create_audio` / the job worker.
4. CLI and MCP must call only `AudioConversationService` (no direct TTS, batching, voice catalog, or translator).
5. Audio creation is async-job only. CLI may poll; MCP returns the job immediately.
