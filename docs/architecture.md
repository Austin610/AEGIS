# Architecture

The CLI and FastAPI application share policy, assurance, persistence and reporting
services. The browser UI is packaged as static assets in the wheel and polls the
API for workspace data and job status.

- `policy.py`: immutable workspace/scope/decision models; exact fixture targets,
  capabilities, exclusions, validity windows and risk ceilings; fail-closed checks.
- `assurance.py`, `gate.py`: deterministic assertions, findings, compatibility,
  baseline classifications and CI decisions with explicit uncertainty.
- `store.py`, `repository.py`: SQLite transactions, schema migrations 1–5,
  evidence hashes, provenance, jobs, audit records, baselines and reversible archives.
- `adapter_contract.py`, `adapters.py`: static typed registry and nine bounded
  offline adapters. No dynamic plugin loading, shell or network target execution.
- `jobs.py`: two cooperative workers, 32 outstanding jobs, timeout/cancellation,
  mode checks and scope checks at submission, start and before persistence.
  Interrupted jobs are recovered as failures on startup.
- `api.py`, `server.py`: local token authentication, expiring browser sessions,
  request size limits, host/origin checks, security headers and loopback binding.
- `web/`: workspace, overview, runs, artifacts, baselines/gate, workflow editor,
  scope and audit screens. All actions use the real API and SQLite data.

Run evidence records sanitized observations and decision references. Original
imported documents are not retained by the adapters. SHA-256 detects accidental
content changes when read; it does not authenticate an author or resist a database
administrator replacing data and hashes together.

SQLite remains the default; `postgres.py` and `transfer.py` provide optional PostgreSQL
transport and atomic migration from SQLite. Scoped local access keys and GitHub OAuth
role mappings provide authorization. Hosted production deployment
qualification remains unverified. Durable offline workers are opt-in and share database leases;
run one API process per database because browser sessions remain process-local.

`workflow.py` derives a bounded, integrity-checked findings snapshot from immutable runs
and SARIF artifacts. Mutable triage and coverage records use optimistic revisions and
transactional audits. Source adapter and policy compatibility determine fixture retests;
SARIF closure requires an analyst to select a newer passing recorded retest and explain
the mapping. Subsequent failures or newer imports can invalidate the closure evidence.

`planning.py` owns work items, sprints, checklists and daily burndown measurements.
Work items do not depend on a sprint and board status is a separate catalog, allowing
a later Kanban implementation to reuse storage and permissions. Completion snapshots
preserve sprint metrics when unfinished work moves to a later sprint.

`data_policy.py` runs manual retention and optional scheduled reversible archival.
`snapshots.py` keeps bounded, short-lived immutable list pages bound to a principal,
workspace and resource. Caches do not survive restart. `readiness.py` records release
acceptance criteria separately from runtime job progress.

`github_auth.py` performs only the explicitly configured GitHub identity exchange;
offline adapters never contact a target. Provider tokens are discarded after sign-in.

`durable_jobs.py` persists queued inputs and atomically commits results with terminal status.
Database-clock leases, claim fencing and bounded retries support multiple offline workers.
Inputs remain in the database until terminal completion; see operations for retention and limits.
