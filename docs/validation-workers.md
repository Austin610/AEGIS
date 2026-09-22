# Durable offline worker acceptance — 2026-09-19

Schema 6 and durable mode are enabled in the local app on port 8766.
The pre-upgrade SQLite backup is `.aegis/backups/before-durable-workers-20260919.db`;
SHA-256: `a06a339c8c8e262de1dcd6e87a58b923d4ed80dff128801ff3a683b96ac1a67e`.
There were zero outstanding jobs before migration. Health reports schema 6 and durable mode.

- Full Python suite: 170 passed, one PostgreSQL-only test skipped.
- PostgreSQL worker/transfer suite: 9 passed, including separate-process races and abrupt commit failure.
- Browser suite with durable mode enabled: 7 passed.
- Ruff, Mypy (33 modules), frontend syntax/format checks: passed.
- Wheel and source distribution: built successfully.

Coverage includes queued work surviving restart, exclusive claims, stale-owner fencing,
three-attempt exhaustion, cross-worker cancellation, scope revocation, atomic result/status
rollback, input integrity, global queue bounds, legacy-mode rejection, owner-only shutdown,
and embedded API execution. The crash test exits the worker after result insertion but
before status persistence, verifies no partial artifact, then retries with another process.

This validates local SQLite and PostgreSQL coordination, not production multi-host operation.
Readiness is 9/14 checkpoints; full blueprint completion is not inferred from this count.
The existing delivery backlog worker item was not updated: automatic approval review
rejected the combined backlog update command without a detailed reason.
