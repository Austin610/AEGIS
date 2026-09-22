# Assurance and Scrum delivery validation — 2026-09-14

This record supersedes earlier platform counts for the additions in this delivery.
The full original blueprint is not complete. In-app acceptance separates implemented
local features/workflows from team integration and production qualification.

## Delivered

- Schema 5 preserves prior SQLite/PostgreSQL data and adds revisioned workflow records.
- Unified historical findings from fixture-derived runs and SARIF imports, source-aware
  deduplication, owners, priorities, tags, notes, workflow status and evidence references.
- Compatible fixture retests; analyst-linked newer passing retests plus rationale for
  SARIF closure; stale closure detection when observations regress or imports supersede it.
- Versioned requirements assessments, workspace evidence checks and escaped HTML reports.
- Scrum backlogs, stories/tasks/subtasks/bugs, scoped parent/finding links, sprint planning,
  configurable duration/capacity, acceptance and Done checks, board, points, burndown,
  original commitment, completion metrics, reviews and retrospective/action notes.
- In-app readiness and an AEGIS product-delivery workspace with 14 release backlog
  items: 8 verified, 6 open. A 14-day sprint is planned, not automatically activated.
- Opt-in scheduled archival, immutable list snapshots, and recorded JSON invariants.
- Configurable GitHub OAuth/PKCE and explicit ID-based roles; GitHub Actions prepared.

## Evidence

- Full Python suite: **162 passed**, one PostgreSQL transfer test skipped in the SQLite
  run and tested separately. Two existing upstream Starlette/AnyIO deprecation warnings.
- PostgreSQL 17.11: **57 selected API/workflow/planning/lifecycle/transfer tests passed**;
  after retest/completion refinements, **16 transfer/workflow/planning/mode tests passed**.
  Counts overlap; they are not presented as an additive total.
- Browser: **7 Playwright workflows passed in Chrome**, including readiness, Scrum,
  acceptance checklists, coverage, finding-to-backlog triage, pagination, retention,
  access-key revocation and SARIF inspection.
- Ruff lint/format, Mypy (32 source modules), JavaScript syntax and Prettier passed.
- Wheel and source distributions rebuilt; assets include the planning screen script.
- In-process load smoke: 200 stored artifacts, 100 paged reads, concurrency 4,
  zero failures; median 13.029 ms, p95 18.101 ms, maximum 23.55 ms on this machine.
  This is a development measurement, not production capacity or an HTTP network benchmark.
- Existing local database backed up before schema upgrade:
  `.aegis/backups/before-scrum-update-20260914-183320.db`, SHA-256
  `b0003977d81380e7ed0fed092bacf2e877aa2880d26a5dcc65b521eb6c47402b`.
- Live app health confirms schema 5. Headless visual verification of the real local
  app recorded no JavaScript errors. Screenshots: `.aegis/verification/scrum-live.png`
  and `.aegis/verification/readiness-live.png`.

## Remaining acceptance

GitHub live OAuth needs app credentials/callback/role mappings. Actions needs a selected
repository and a successful remote run. Docker is absent on this machine. No public
deployment, production capacity qualification, or distributed-worker implementation
is claimed. Permanent deletion is a documented design only. Each mode has a verified
offline workflow, but the broader specialized blueprint milestones remain open.

The application and local data persist. Development does not continue in the background
merely because the dashboard is open; the readiness view is a recorded acceptance status.
