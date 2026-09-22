# Permanent deletion lifecycle

SQLite and PostgreSQL archived-record deletion require explicit server configuration.
Deletion remains disabled in the live local app. Automatic retention only archives.
The bounded local lifecycle checkpoint is verified; production operations remain separate.

## Configure and review

Set AEGIS_DELETION_GRACE_DAYS to an integer from 1 to 36500 before starting the
server. This server-wide setting is the minimum time since archival, not
run creation. The server ignores proposed preview grace when configured. Omit the
setting to disable apply. PostgreSQL additionally requires AEGIS_POSTGRES_BIN pointing to the directory
containing compatible pg_dump and pg_restore executables. Both options are required.

In Data policies, assess archived runs, review exact IDs, checksums, references and
estimated payload sizes, then choose Prepare deletion and verify recovery. The
server makes an integrity-checked SQLite snapshot and restores it to a new file,
or verifies a native PostgreSQL dump in a newly created database.
Only successful recovery verification issues an approval. Type the displayed
confirmation to permanently delete the reviewed eligible runs and exclusive evidence.
No generic cascade, decision deletion or automatic deletion occurs.
All three API operations (preview, prepare, apply) require an administrator.

Approvals expire after five minutes, are bound to the administrator and workspace,
are held only in memory (at most 32), and disappear on server restart. Apply rechecks
the fingerprint, configuration, references, grace, holds and active jobs under a
write transaction. Changes reject apply. Result deletion, minimal checksummed
tombstone and audit commit together; failed commits roll back. Successful apply
consumes the approval and invalidates workspace list snapshots. Replays fail.

## Protected history and retained copies

Baseline runs, shared evidence, active jobs and recognized holds are protected.
Any triage, coverage or finding-linked planning item conservatively protects the
workspace's run history, including grouped finding IDs that do not embed a run ID.
Other references are conservatively matched in workflow, job, artifact and run
payloads. Ordinary text containing an ID may block deletion. The first 100 archived
runs are examined; oversized reference inventories fail closed at 5000 records per
source or 10 MiB combined payloads. Holds have no management UI yet.

Recovery copies remain in deletion-recovery beside the SQLite database, as uniquely
named .backup.db and .restored.db files. Failed preparation can leave a backup.
They contain deleted content and access-key revocation state. Protect them like the
original database. There is no automatic file deletion: operators must inventory these
files and exported bundles, establish a retention deadline, and retire copies under
their approved retention policy. No user backups or exported copies are erased by
AEGIS. Restoring an old copy reintroduces deleted data and potentially revoked access;
restore in isolation and review access before service. SQLite page reuse or VACUUM
is not a forensic erasure guarantee for SSDs, filesystem history or backups.

Tombstones contain IDs, hashes, timestamp, actor and recovery checksum, not deleted
source payloads. Backup validation checks tombstone integrity. Hashes detect changes;
they do not resist a database administrator replacing both content and hashes.

## Remaining acceptance

Finer-grained shared
history management, hold management, external-copy retirement and broader
production failure qualification remain outstanding. Do not count this as complete
permanent deletion across all AEGIS data.

Tests cover typed confirmation, permissions, actor/workspace binding, expiry, replay,
changed references/baselines/policy/holds/jobs, evidence integrity, grouped history,
rollback after deletion but before audit, failed recovery, actual restored records,
tombstone integrity, cache invalidation and coverage validation racing with deletion.
The disposable browser fixture exercises a complete SQLite deletion; the live user
database is never used for destructive tests.

Validation on 2026-09-20: 185 Python tests passed (one PostgreSQL-only transfer
test skipped); eight browser workflows passed. PostgreSQL assessment/workflow
checks: eight passed, ten SQLite-only deletion/recovery checks explicitly skipped.
Ruff, Mypy (34 modules), frontend checks and package build passed.


## Native PostgreSQL recovery

The configured PostgreSQL account needs permission to read the AEGIS schema and
CREATE DATABASE for the recovery exercise. This is an elevated operational privilege;
use only a trusted, dedicated deployment and perform preparation during a maintenance
window. AEGIS serializes source metadata while exporting the snapshot, which can
pause jobs and cause leases to expire. Each native tool invocation has a 120-second
limit. Matching PostgreSQL 17 client tools were tested locally; incompatible tools
fail preparation. No approval is issued when dump, restore or inventory validation fails.

The exercise uses pg_dump custom format with an exported consistent snapshot of the
current schema. It creates a random aegis_recovery_ database from template0, restores
there with pg_restore in one transaction, and verifies table contents, insertion
sequences, evidence hashes, queued input and tombstone integrity. It then removes
only that newly created database. Existing databases are never restore targets.
Cleanup is attempted on failure too; if database permissions or connectivity prevent
cleanup, an operator must inspect the generated recovery databases. Do not indiscriminately
drop databases by prefix. Dump archives remain in the configured recovery directory.
This is AEGIS-schema recovery, not cluster disaster recovery: roles, ownership, ACLs,
other schemas and external dependencies are outside the verified restore.

The bounded inventory accepts up to 50000 rows per table and 50 MiB of serialized
table contents. Only straightforward libpq URL options for host, port, user, password,
database, SSL and search_path are accepted. Credentials are passed through the child
process environment, never command arguments or API error messages. Keep the local
host and configured executable directory trusted.

A standalone exercise is available with AEGIS_DATABASE_URL set:

```powershell
.\.venv\Scripts\aegis.exe verify-postgres-recovery .aegis/recovery --bin-dir PATH_TO_POSTGRES_BIN
```

This command retains the archive and prints its checksum; it does not enable deletion.
The web preparation operation performs its own fresh recovery exercise. For native
integration tests set AEGIS_TEST_POSTGRES_URL and AEGIS_TEST_POSTGRES_BIN. Without
native tooling those tests skip explicitly; a skip is not recovery acceptance.

Native recovery validation on 2026-09-20: 19 PostgreSQL deletion/recovery/workflow
checks passed with PostgreSQL 17 tools (one SQLite-only check skipped). The full
SQLite suite passed 185 tests, with three PostgreSQL-only checks skipped. Eight
browser workflows passed after fixing workspace-switch refresh interference.
Native restore failure and mismatched inventory both withheld approval, preserved
source records and cleaned up the newly created recovery database.


## Archived artifacts

Data policies now accepts an artifact ID for reversible archive/restore, and offers
Runs or Artifacts as separate deletion selections. Artifact archive markers are
workspace-scoped and require an administrator. Repeated archive requests preserve
the original archive time; restoring then archiving starts a new grace period.
Archiving does not hide artifacts or change their findings: this protects history.
Only confirmed deletion removes the selected payloads.

Artifact preview checks source checksums and references in artifacts, runs, evidence,
decisions, jobs and workflow records. Triage, coverage and finding-linked planning
conservatively protect workspace history, including grouped SARIF findings. Holds
and active jobs also block deletion. In particular, a completed job referencing its
output protects that artifact until job lifecycle work explicitly handles it.
At most 100 archived artifacts (ordered by ID) are assessed per preview; dependency
inventories are bounded to 5000 rows per table and 10 MiB of payloads. Oversized
inventories reject assessment rather than assume records are unreferenced.

Artifact approvals use a separate fingerprint and typed phrase. The same SQLite or
native PostgreSQL recovery prerequisite applies. Apply rechecks dependencies under
a write transaction, removes artifact and archive marker together, writes a minimal
checksummed tombstone and audit, and invalidates workspace snapshots. Failure rolls
back the payload, archive marker and tombstone together. Backups and exports retain
copies; managed-copy expiry is available; see recovery-expiry.md.

Artifact validation on 2026-09-20: full Python suite 194 passed with three
PostgreSQL-only checks skipped; nine browser workflows passed. PostgreSQL run/artifact
checks passed 21 tests (one SQLite-only check skipped); the final artifact suite
passed all nine tests, including rollback and resource-selection binding. Lint,
36-module type checks, frontend checks and wheel/source builds passed.

Managed backup expiry is now implemented and reviewed in Data policies. See
[expiry controls and limitations](recovery-expiry.md). Older unmanaged copies and
exports require operator handling. Live deletion and expiry remain disabled.
