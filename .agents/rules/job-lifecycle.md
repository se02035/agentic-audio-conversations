---
trigger: always_on
description: >-
  Table-driven job FSM: transition-only status.json; no progress or stale auto-fail.
---

# Why this rule exists

Status churn (progress/heartbeat) and stale auto-fail caused false failures on long TTS jobs. Do not reintroduce them.

# Job lifecycle

1. Allowed edges live in `ALLOWED_TRANSITIONS` (`logic/jobs/models.py`). Illegal transitions are no-ops.
2. Persist `status.json` only on real status transitions (queued → running → succeeded|failed|cancelled).
3. No `progress` field and no per-batch status writes.
4. No heartbeat / stale auto-fail. Orphaned `queued`/`running` after restart stay as-is until cancelled or replaced.
5. Cancel is cooperative between TTS batches; after restart, cancel only writes GCS `cancelled`.
6. GCS layout: `…/scripts/{id}/script.yaml` and `…/jobs/{id}/status.json` + `…/jobs/{id}/output/audio.wav`.
