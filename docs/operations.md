# Local operations

## Access

The original `access-token` is the owner credential. Keep it private. In **Access
management**, an administrator can issue named credentials:

| Role | Permissions |
| --- | --- |
| Reader | View selected workspaces and export their bundles when policy permits |
| Analyst | Reader permissions plus workflows, triage, coverage, Scrum, baselines, gates and archive/restore |
| Administrator | All workspaces, workspace creation, scope, data policies and access administration |

Reader and analyst credentials require an explicit set of workspace UUIDs.
Administrators have global scope. Tokens appear once when created; only SHA-256
digests are stored. Revocation blocks subsequent token and cookie requests.
Sessions expire after eight hours or a server restart. Revoking a key does not
undo completed actions or cancel jobs already accepted; cancel those jobs separately.
The local owner credential cannot be revoked through this interface.

Workspaces are filtered before pagination. Enforcement is server-side. Write controls are disabled for readers and administrative controls are hidden or
disabled for other roles; the API independently rejects unauthorized operations.
This remains a local, trusted-OS deployment. GitHub sign-in is optional and requires
explicit OAuth and role configuration; see [GitHub connection](github-connection.md).
Public hosting remains separate deployment work.

## Retention and exports

**Data policies** configures a run age and workspace bundle exports. Retention is
disabled by default. Preview lists at most 500 eligible run IDs and excludes every
baseline run. Applying requires a matching preview less than ten minutes old.
Changed policy, eligibility or baseline protection invalidates the preview.
Archive and its audit record commit together. Archiving never deletes evidence;
restore individual runs from the archived-run view. An optional automatic interval
in hours enables scheduling while AEGIS runs. It remains off by default. The scheduler
checks every minute, archives at most 500 eligible runs per due workspace, and persists
its last execution time across restarts. Further batches wait for the next interval.
Baseline protection, archive changes, timing and audit commit in one transaction.

Bundle exports include the workspace, scope, runs, decisions, baselines and archive
metadata, triage, coverage, Scrum items, sprints and burndown records. Evidence and
artifacts are included when enabled. Each stored evidence
hash is checked; the bundle includes a SHA-256 manifest. The bundle is a consistent
transactional snapshot, capped at approximately 10 MiB. Exports are audited and
exclude credentials, jobs and audit history. Use database backups for complete recovery.
Bundle restrictions do not prevent an authorized viewer from copying visible records
or downloading existing individual reports. There is no automatic hard deletion.

## SQLite backup and restore

```powershell
.\.venv\Scripts\aegis.exe backup .aegis/app/aegis.db .aegis/backup-2026-09-14.db
.\.venv\Scripts\aegis.exe restore .aegis/backup-2026-09-14.db .aegis/recovered.db --sha256 CHECKSUM_FROM_BACKUP
```

Both commands require a new destination file. Backup uses SQLite's snapshot API,
checks database integrity, foreign keys and evidence hashes, then prints the file
checksum. Restore verifies that checksum and never overwrites the live database.
Stop the application before manually replacing its database with a verified restore.
Protect backups like the original database: they include credential digests and
confidential observations. Preserve the owner token separately. Restoring an old
backup also restores its access-key revocation state; review/revoke credentials
before using the restored application. Browser sessions are not backed up.

## PostgreSQL

Install the optional driver using `pip install -e '.[postgres]'`. Set
`AEGIS_DATABASE_URL` to a PostgreSQL connection URL before starting `aegis serve`.
Store credentials outside source control. A fresh database receives transactional,
versioned schema migrations. The default remains SQLite.

To transfer an existing current-schema SQLite database, stop the application,
back it up, set `AEGIS_DATABASE_URL`, and run:

```powershell
.\.venv\Scripts\aegis.exe migrate-postgres .aegis/app/aegis.db
```

The destination must have no AEGIS records. The transfer verifies source integrity,
preserves identifiers and insertion order, and commits all tables together. It
never changes the SQLite source. Verify the resulting data before switching over.
PostgreSQL backup/recovery uses the database's native `pg_dump`/`pg_restore` tooling;
the AEGIS backup command is specifically for SQLite.

Run one API process per database; browser sessions are process-local. Additional
offline workers are supported in durable mode below. PostgreSQL operations serialize with
an advisory transaction lock; this preserves metadata consistency but is not a
high-throughput design. Do not allow other applications to write these tables.

The optional Compose storage profile starts PostgreSQL without a host port. Set
`POSTGRES_PASSWORD` and a matching `AEGIS_DATABASE_URL` using host `postgres`, then
run `docker compose --profile storage up --build`. Wait for PostgreSQL readiness
before starting the app. Container and remote CI validation are tracked separately.

## Verification

`npm run test:browser` uses a disposable database and a loopback test server on
port 8878. Windows defaults to installed Chrome. On other systems run
`npx playwright install chromium` first. The test token authenticates only to this
temporary server. Backend tests use SQLite by default. Set
`AEGIS_TEST_POSTGRES_URL` to a dedicated test database to run `tests/test_api.py`
against PostgreSQL; each test creates and removes its own randomly named schema.

Implementation references: [PostgreSQL INSERT](https://www.postgresql.org/docs/17/sql-insert.html)
and [Psycopg transaction handling](https://www.psycopg.org/psycopg3/docs/basic/transactions.html).

## Durable offline workers (schema 6)

Back up the database, drain existing jobs, and stop the old API before enabling:

```powershell
$env:AEGIS_DURABLE_JOBS='1'
.\.venv\Scripts\aegis.exe serve --data-dir .aegis/app --port 8766
```

The API runs two embedded workers by default. In another terminal with the same
configuration, start additional workers with `aegis worker --data-dir .aegis/app`.
Set `AEGIS_EMBEDDED_WORKER=0` on the API to use only separate worker processes.
SQLite workers must use the same local database file; use the same
`AEGIS_DATABASE_URL` for PostgreSQL workers. Use matching code and adapter versions.
Activation persists in the database: legacy mode then refuses startup. Rollback
requires restoring the pre-upgrade backup with all processes stopped; this discards
subsequent changes. Do not edit the runtime flag manually.

The global queue holds at most 32 outstanding jobs. Each process executes at most
two jobs, with a 10-second cooperative execution deadline, 30-second database-clock
lease and three total claims. Expired claims are retried; exhausted claims fail.
Shutdown releases only that worker's claims. Cancellation can be requested through
the API regardless of which worker owns the job. A stale or cancelled worker cannot
commit results. Computation may repeat, but each job commits results at most once.
CPU-blocking work may overrun the deadline and lose its lease. This queue supports
only the bounded offline adapters, not arbitrary commands or external side effects.

Queued input is persisted unencrypted (maximum 1 MiB per job), integrity-hashed,
and removed from the queue on terminal completion. Known credential patterns are
rejected; redact sensitive input before submission. Pattern detection is not a
complete secret detector. Backups may retain old queue contents. Result evidence
and audit records retain their existing lifecycle. Scope is rechecked before claim
and commit, and the original submitter remains the audit actor.

Drain and stop all workers before database transfer. Restored outstanding jobs may
execute again against the restored database. Test recovery in isolation before
switching a restored database into service. SQLite and PostgreSQL process races,
crashes during commit, cancellation, retry exhaustion and browser workflows pass
local tests; production capacity, multi-host failure and hosted operations remain
unqualified.

## Reviewed archived-run deletion

Deployments can explicitly configure `AEGIS_DELETION_GRACE_DAYS` to enable
reviewed deletion in Data policies. It is disabled by default and remains disabled
in the local user app. Read [the lifecycle and recovery requirements](deletion-lifecycle.md)
before enabling. Recovery copies retain deleted data and require an operator retention policy.
PostgreSQL additionally requires `AEGIS_POSTGRES_BIN` and a database account capable
of creating a temporary recovery database. Reviewed deletion also supports archived, unreferenced artifacts; select Artifacts
in Data policies. Managed recovery-copy expiry is documented in [recovery-expiry.md](recovery-expiry.md).

Set `AEGIS_RECOVERY_RETENTION_DAYS` only when ready to enable reviewed expiry of
registered recovery copies. The newest intact generation, active approvals and holds
remain protected. This does not enable scheduled deletion or remove external copies.


## Real-process operational drill

Run `.venv/Scripts/python.exe scripts/operational_drill.py` to exercise a disposable
schema-5 upgrade, real loopback HTTP, 12 durable jobs at concurrency four, 100 reads,
process restart, verified backup restore into a new directory, and persistence of
credential revocation. It uses no live database or user credential. The prepared
GitHub Actions workflow and local verification script include this drill.
Results are written to `.aegis/verification/operational-drill.json`.

Validated on 2026-09-20: all checks passed. The synthetic read sample had a 7.436 ms
median and 26.582 ms p95. This does not establish production capacity, multi-host
recovery or high availability. The local launcher now defaults to durable jobs while
respecting an explicitly configured mode. Drain legacy jobs before changing modes.
