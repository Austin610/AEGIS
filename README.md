# AEGIS

A local assurance workbench with a working web dashboard, authenticated API,
SQLite or PostgreSQL evidence storage, scoped access, offline analysis workflows,
baseline comparisons and CI gates.
It also includes a findings/remediation inbox, requirements coverage, stakeholder
reports, Scrum backlogs and sprint tracking, and an in-app readiness page.
Recorded observations are supplied by the user or imported from existing results.
The complete M0–M14 blueprint is still in progress; see [roadmap](docs/roadmap.md).

## Start the app

Requires Python 3.12 or later. From the project directory in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\aegis.exe serve --data-dir .aegis/app --port 8766
```

Open http://127.0.0.1:8766 and sign in using the token in
`.aegis/app/access-token`. Keep that file private. Existing data and the token
persist across restarts; browser sessions require signing in again after restart.
`scripts/start-local.ps1` starts an already installed environment.
On Linux/macOS, use `.venv/bin/python` and `.venv/bin/aegis`.

Use **Load synthetic example** for three clearly labeled runs showing
PASS → FAIL → PASS. Create a workspace and define a time-bounded scope before
submitting your own workflows. Scope targets use exact `fixture://` identifiers.
The dashboard includes evidence inspection, HTML reports, JSON export, named
baselines, gate evaluation, paginated history, cancellation, archive/restore and audit.
Data policies provide retention previews and workspace exports. Access management
issues revocable reader, analyst and administrator credentials. See
[operations](docs/operations.md) for permissions, backup/restore and PostgreSQL migration.

Open **Backlogs & sprints** for project management and **Build & readiness** for
acceptance progress. [Scrum guide](docs/scrum.md) describes the board and planning
rules. [GitHub connection](docs/github-connection.md) lists the exact settings needed
for GitHub sign-in and Actions; neither depends on choosing a repository during
local development. **Freeze list pages** provides stable short-lived browsing.

## Available workflows

| Adapter | Result |
| --- | --- |
| Recorded JSON invariants | Declarative comparisons over supplied observations, with unknown/error outcomes |
| SARIF import | Recorded analyzer results, locations, suppression states and provenance |
| Analyst notebook | Sanitized analyst observations |
| OpenAPI inventory | Offline operation and declared security metadata |
| File metadata | Hash, size and format hints; no file execution |
| Research evaluation | Precision, recall, F1, coverage and unknown counts |
| JUnit import | Recorded test outcomes with provenance |
| Declared invariants | Expected versus supplied observed outcomes |
| Metric thresholds | Supplied metrics compared with minimum and maximum thresholds |

All nine named modes have a notebook workflow; specialized adapters are restricted
by mode. The presence of a mode does not mean its entire blueprint workflow exists.
No live target scanning, vulnerability reproduction or exploit execution is implemented.

## CLI and CI gate

```powershell
.\.venv\Scripts\aegis.exe assurance demo .aegis/my-demo
.\.venv\Scripts\aegis.exe assurance --help
.\.venv\Scripts\aegis.exe assurance gate WORKSPACE_UUID BASELINE RUN_UUID --database .aegis/app/aegis.db
```

The demo creates a new directory and refuses to overwrite an existing one.
Gate exit codes are **0 pass**, **1 blocking regression**, **2 uncertainty or
configuration error**. Defaults block new high/critical regressions. Use
`--fail-on-any-violation` to block all current failures. Compatibility checks must
succeed before comparison. Passing means no blocking recorded regression under
that policy; it is not proof that an application is secure.
See [offline workflow](docs/offline-workflow.md) for individual import commands.

## Development and deployment

```powershell
npm ci --ignore-scripts
.\scripts\verify.ps1
```

The script runs tests, lint, formatting, type checks, a wheel/source build and
frontend syntax/format checks. `requirements-dev.lock` records the tested Python
environment as version constraints. It is not a hash-locked cross-platform lockfile.
CI configuration covers Windows/Linux Python 3.12 and a container build. Remote CI
and Docker execution have not been verified on this machine.

`docker compose up --build aegis` uses a non-root container and a persistent data
volume, publishing only on host loopback. The optional PostgreSQL profile can be selected with `AEGIS_DATABASE_URL`;
SQLite remains the default. See the operations guide before switching databases.
Do not expose the container port publicly. See [security](docs/security.md),
[architecture](docs/architecture.md), and [development](docs/development.md).
