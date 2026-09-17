# Conversation script schema

YAML or JSON. `validate_script` checks this schema only (no `list_voices`). One or two speakers. Top-level keys are **only** `metadata`, `voices`, and `turns`.

Copy-and-adapt templates (not the 15-minute repo `templates/`):

- Two speakers: [assets/sample-dialogue.yaml](../assets/sample-dialogue.yaml)
- One speaker: [assets/sample-narration.yaml](../assets/sample-narration.yaml)

Schematic:

```yaml
metadata:
  title: Tech Pulse Europe
  description: Optional episode description
  language_code: en-US
  audio_encoding: LINEAR16
  sample_rate_hertz: 24000
voices:
  Host:
    name: en-US-Chirp3-HD-Fenrir
    language_code: en-US
  Guest:
    name: en-US-Chirp3-HD-Aoede
    language_code: en-US
turns:
  - speaker: Host
    text: Welcome to the show.
    pause_after_ms: 300
  - speaker: Guest
    text: Glad to be here.
```

## Field map

### `metadata`

| Field | Required | Default | Notes |
| --- | --- | --- | --- |
| `title` | yes | — | Episode title |
| `description` | no | omitted | Short episode blurb |
| `language_code` | no | `en-US` | BCP-47; must match voice locales |
| `audio_encoding` | no | `LINEAR16` | Declared only; synthesis always writes LINEAR16 WAV |
| `sample_rate_hertz` | no | `24000` | 8000–48000 |

### `voices`

Map of 1 or 2 speaker aliases. Each alias is `[a-zA-Z0-9]+` (no spaces, dashes, or underscores). Defaults: `Host` / `Guest` (dialogue) or `Narrator` (narration).

Each value:

| Field | Required | Notes |
| --- | --- | --- |
| `name` | yes | Full Chirp 3 HD id; must contain `Chirp3-HD` |
| `language_code` | yes | Same BCP-47 as `metadata.language_code` |

Do **not** declare a Companion voice. One-speaker scripts get an unused Companion persona at synthesis time.

### `turns`

Non-empty ordered list. Each item:

| Field | Required | Notes |
| --- | --- | --- |
| `speaker` | yes | Must be a key in `voices` |
| `text` | yes | Non-empty spoken sentences. No SSML, markdown, or stage directions |
| `pause_after_ms` | no | Silence after the turn (`>= 0`). Typical 250–400. Flushes a TTS batch |

Keep each turn well under 1500 characters (server batches at that cap). Prefer 6–12 short turns for an overview, not a 15-minute script.

## How to fill a template

1. Copy [sample-dialogue.yaml](../assets/sample-dialogue.yaml) or [sample-narration.yaml](../assets/sample-narration.yaml).
2. Set `metadata.title`, optional `description`, and `language_code`. Keep `LINEAR16` / `24000` unless asked otherwise.
3. If the language changes, rewrite every voice `name` and `language_code` to match (examples below).
4. Rewrite `turns` for the topic. Alternate speakers for dialogue; keep one `Narrator` for narration.
5. Confirm with the user, then `upload_script` / `validate_script`.

Dialogue skeleton: two `voices` keys, turns alternate `Host` then `Guest`.

Narration skeleton: one `Narrator` voice; every turn uses `speaker: Narrator`.

## Example Chirp 3 HD names (v1, no list_voices)

Match the `language_code` prefix to `metadata.language_code`:

- `en-US`: `en-US-Chirp3-HD-Fenrir`, `en-US-Chirp3-HD-Aoede`
- `en-GB`: `en-GB-Chirp3-HD-Fenrir`, `en-GB-Chirp3-HD-Aoede`
- `de-DE`: `de-DE-Chirp3-HD-Fenrir`, `de-DE-Chirp3-HD-Aoede`

If `start_conversation` fails because a name is missing from the EU catalog, pick the other example for that language, re-upload, and validate again.

## Common `validate_script` failures

- Extra top-level keys, or missing `metadata` / `voices` / `turns`
- Speaker alias with spaces, dashes, or underscores
- Turn `speaker` not in `voices`
- Empty or whitespace-only `text`
- Voice `name` without `Chirp3-HD`
- More than two voices
- Zero turns

`validate_script` does not call `list_voices`. A schema-valid name can still fail later at `start_conversation` if it is absent from the EU catalog.
