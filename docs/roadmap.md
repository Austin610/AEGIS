# Roadmap and delivery status

The full supplied blueprint is **not complete**. The current delivery is a usable,
local defensive platform. Progress on the live build screen counts concrete local
implementation checkpoints, not percentage completion of the entire blueprint.

## Implemented

- Python package, configuration validation, structured logging and diagnostics.
- Nine workspace modes with explicit offline capabilities and exact scope policy.
- SQLite migrations, sanitized evidence, findings, provenance and hash verification.
- Deterministic invariant evaluation, compatible baselines, HTML/JSON reports.
- Nine offline adapters, including declarative recorded JSON invariants, plus bounded jobs and recovery.
- Authenticated local API and browser dashboard with persistent workspace data.
- Research metrics, JUnit and SARIF imports, metadata inventory and min/max threshold evaluation.
- Reversible archive/restore, audit history and CI gates with uncertainty outcomes.
- Bounded API and dashboard pagination for workspace, run, job, artifact, audit and baseline lists.
- Scoped reader/analyst/admin credentials, hashed tokens, revocation and actor attribution.
- Retention previews, baseline protection, reversible batch archiving and controlled exports.
- Verified SQLite snapshots and restore-to-new-file tooling.
- Optional PostgreSQL persistence, versioned migrations and atomic SQLite data transfer.
- Repeatable browser workflow tests against a disposable database.
- Wheel/source packaging, local launcher, verification script and CI configuration.
- In-app Build & readiness, with feature/workflow/team/production acceptance separated.
- Findings inbox, revision-checked triage, retest linkage, recurrence flags and stakeholder reports.
- Versioned framework/control assessments with required evidence for pass/fail claims.
- Scrum product/sprint backlogs, stories/tasks/subtasks/bugs, priorities, assignees, points,
  acceptance and Done checklists, five-column board, configurable sprint duration,
  capacity, original commitment, burndown/progress, reviews and retrospectives.
- Opt-in scheduled archiving with persistent cadence and baseline protection.
- Five-minute immutable list snapshots bound to principal/workspace/resource.
- Configurable GitHub OAuth/PKCE sign-in with explicit numeric-ID role mappings; locally tested.
- Permanent deletion lifecycle design, and repository-independent GitHub Actions configuration.

## Remaining blueprint work

M0 foundation is implemented. The broader M1–M14 milestones remain partially
implemented or pending; feature presence above is not full milestone acceptance.
Remaining engineering includes deeper specialized mode workflows, snapshot enumeration
for the workspace selector, distributed durable workers, a validated permanent-deletion
implementation, and production operational/load qualification. GitHub live sign-in
needs OAuth configuration; remote CI needs a selected repository and an actual run.
Docker is unavailable on this machine. Local mock-provider tests and development
load measurements do not establish hosted team or production readiness.

The automated offensive discovery, exploitation and live vulnerability reproduction
parts of the original blueprint are excluded from this implementation. Offline
records and synthetic examples are labeled accordingly.

Earlier validation documents describe historical checkpoints. The current platform
validation record is `validation-platform.md`; `progress.json` is a static verified
snapshot. The local live screen polls `.aegis/live/status.json` during development.
The primary in-app progress view is `http://127.0.0.1:8766/#readiness`.
See `scrum.md`, `github-connection.md`, and `deletion-lifecycle.md` for current workflows,
connection requirements, and the intentionally unimplemented destructive lifecycle.
