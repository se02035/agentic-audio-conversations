# Library API

Primary entry: `AudioConversationService` in `tts_audio_conversation.logic`.

```python
from pathlib import Path

from tts_audio_conversation import create_audio_conversation_service_from_adc
from tts_audio_conversation.logic.settings import Settings

service = create_audio_conversation_service_from_adc(Settings())
uploaded = service.upload_script(Path("episode.yaml"))
# optional:
translated = service.translate_script(uploaded.script_uri, "de-DE")
uri = translated.output_uri
job = await service.create_audio(uri)
# poll:
record = await service.get_job(job.job_id)
# optional download:
service.download(record.audio_uri, Path("episode.wav"))
```

Or wire credentials explicitly with `create_audio_conversation_service(settings, credentials, project_id)`.

## Methods

| Method | Input | Notes |
| --- | --- | --- |
| `upload_script` | local `Path` or inline YAML/JSON | Writes `…/scripts/{id}/script.yaml` |
| `validate_script` | `gs://` URI | Soft result (`valid=False` + `error`); no `list_voices` |
| `list_voices` | optional language | EU Chirp 3 HD catalog |
| `translate_script` | `gs://` URI + target language | Writes sibling `script.{lang}.yaml`; returns `output_uri` |
| `create_audio` | `gs://` URI | Async job only; returns before `synthesize_speech` |
| `get_job` / `cancel_job` | `job_id` | In-memory or GCS `status.json` |
| `download` | `gs://` + local path | No residency enforcement |

Processing after upload always takes a `gs://` script URI. Translation is never embedded in `create_audio`.

## GCS layout

```text
gs://{bucket}/{prefix}/
  scripts/{script_id}/script.yaml
  scripts/{script_id}/script.{lang}.yaml   # from translate_script
  jobs/{job_id}/status.json
  jobs/{job_id}/output/audio.wav
```

Prefix defaults to `conversation` (`AUDIO_CONVERSATION_GCS_PREFIX`).

## Errors

| Call | Invalid input | Behavior |
| --- | --- | --- |
| `validate_script` | bad/missing URI or payload | Soft: `ScriptValidationResult(valid=False, error=…)` — never raises |
| `upload_script` | empty/oversized/invalid YAML/JSON | Raises `ScriptPayloadError` |
| `translate_script` / `create_audio` / `download` | non-`gs://` or missing object | Raises `ScriptPayloadError` |
| `create_audio` | unknown Chirp 3 HD voice | Raises `VoiceCatalogError` (before enqueue) |
| `get_job` / `cancel_job` | unknown id | Raises `JobNotFound` |
| Worker TTS / upload failure | after job started | `JobRecord.status == failed` with `error` string (no raise to caller of `create_audio`) |
| Cooperative cancel | between batches | `JobRecord.status == cancelled` |
