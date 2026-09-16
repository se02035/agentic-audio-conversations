Create spoken conversation audio from YAML or JSON scripts using Google Cloud Chirp 3 HD voices on the EU Text-to-Speech endpoint. Scripts and output WAVs are stored in GCS.

## Workflow

1. Call `upload_script` with the YAML/JSON body. Use the returned `script_uri` for later tools. Skip this step only when the user already has a `gs://` script URI.
2. Call `validate_script` with that `script_uri`. If `valid` is false, explain the `error` field and stop. Do not start synthesis.
3. Call `translate_script` only when the user asked for another language. Pass the returned `output_uri` to synthesis, not the original URI.
4. Call `start_conversation` with the script URI to synthesize. It returns immediately with `job_id` and `status: queued`. Do not assume the WAV is ready.
5. Poll `get_conversation_status` with that `job_id` until `succeeded`, `failed`, or `cancelled`. Wait several seconds between polls; synthesis can take minutes.
6. On success, report the `audio_uri` (`gs://`). There is no download tool — the client fetches the WAV from GCS with its own credentials.
7. Call `cancel_conversation` only when the user asks to stop a queued or running job.

## Script rules

- Voice names must contain `Chirp3-HD`.
- Speaker aliases must be alphanumeric (`[a-zA-Z0-9]+`).
- Scripts have 1 or 2 speakers.
