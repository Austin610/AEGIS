# Managed recovery backup expiry

Expiry is disabled by default. Set AEGIS_RECOVERY_RETENTION_DAYS (1-36500) before
starting the server to configure a minimum age for review. This is separate from
AEGIS_DELETION_GRACE_DAYS. The live local app has neither destructive policy enabled.
There is no scheduled file deletion: an administrator reviews and confirms one file
at a time in Data policies / Recovery backup expiry. This view is server-wide.

Only successful recovery exercises created by this implementation register their
files in the database audit journal. Receipts include the managed directory identity,
creation time, file name, byte count and SHA-256, never source contents. SQLite backup
and restored copies are registered together; native PostgreSQL exercises register
their retained custom-format dump. Registration is internal, not a public enrolment
endpoint. Failed exercises and older files without receipts stay unmanaged.

Preview checks age, file contents and protections. The newest complete, intact
recovery generation is always kept even after its retention age. Active deletion
approvals protect their recovery generation. Any recognized workspace hold protects
all copies because backups may contain multiple workspaces. Missing or altered files,
invalid paths, symlinks/junctions, hardlinks and duplicate registrations fail closed.
If no complete recovery remains, no expiry is permitted. Inventory bounds are 128
receipts and 512 MiB of files; exceeding them requires operator review rather than
silently ignoring older files. The expiry reconciliation journal is bounded to 1024
intent/completion events. These bounds make this suitable for the local workbench,
not an unattended enterprise backup service.

Prepare binds a five-minute, single-use review to the administrator, exact file,
policy and inventory fingerprint. Apply requires typing EXPIRE followed by the exact
file name. It rechecks the inventory under the database write lock and the application
recovery lock. New backups, holds, policy changes, modified contents or newly active
approvals invalidate the review. The server supports one API process per database;
other programs must not mutate the managed recovery directory concurrently.

Filesystem removal cannot be rolled back by a database transaction. A durable audit
intent is committed before removal. Successful removal receives a completion event.
If the file was removed but the completion commit failed, the API reports
file_removed_audit_pending. The next preview reconciles incomplete intents without
removing anything: it records either file_absent_on_reconciliation or
file_present_requires_new_review. A retained file always needs a new review. Existing
approvals do not survive restart. Recent activity is visible in the expiry panel.

This feature does not delete exported bundles, arbitrary/manual backups, filesystem
snapshots, cloud copies or the minimum recovery generation. Operators must inventory
those separately, establish their retention deadlines and legal holds, and retire
them through their storage provider's process. Do not enrol unmanaged files by editing
the audit database. If all copies must be erased, the minimum-recovery safeguard means
that request requires an operator procedure outside this tool. File unlinking does
not promise forensic erasure, SSD overwrite or deletion of external replicas.

The local lifecycle checkpoint covers reviewed database deletion and managed-copy
expiry with these limitations. It does not establish production operational acceptance
or a legal/compliance erasure guarantee.

Validation on 2026-09-20: 205 Python tests passed (three PostgreSQL-only tests
skipped); 11 expiry tests passed against PostgreSQL. Ten browser workflows passed,
including review, typed confirmation and expiry of a disposable recovery copy.
Ruff, 37-module type checks, frontend checks and package builds passed.
