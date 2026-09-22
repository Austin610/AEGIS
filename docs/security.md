# Security and trust boundaries

This release runs under a trusted OS account on a local machine. The API binds
to loopback by default and requires a random local token. Browser sessions expire
after eight hours and on server restart; session cookies are HttpOnly and SameSite
Strict. Cookie-authenticated writes require a custom request header. Host/origin
checks and content security headers reduce accidental browser exposure.

The owner token grants administrator access to all workspaces. Named credentials
can grant administrator access or reader/analyst access to explicit workspaces.
The API enforces these scopes and roles, filters lists before pagination, and
checks revocation on token and cookie requests. Credential hashes are persisted;
browser sessions remain in memory. Revocation does not cancel accepted jobs.
The access-token file is created with mode 0600 where supported. Windows users must
rely on and verify the data directory's Windows ACLs. Plain HTTP is intended only
for loopback. The explicit container binding relies on the supplied loopback host
port mapping; public deployment and TLS termination are not implemented or validated.

Scope accepts only exact fixture identifiers, not network destinations. Jobs recheck
scope before execution and before result storage. The registry contains only built-in
offline adapters. Job limits and cancellation are cooperative, not a sandbox for
untrusted executable code. Imported files are parsed as data and never executed.

Requests and imports have size limits. Sanitization covers known secret patterns,
not arbitrary sensitive content. Reports escape untrusted text. Original input files
remain with the user; persisted sanitized derivatives may still contain confidential
observations. Protect the database and backups accordingly.

Evidence hashes are checked on reads, but an OS/database administrator can modify
both content and hashes. Audit records are local records, not independently signed or
externally immutable logs. Archive/restore changes visibility without deleting data;
age-based retention previews can archive eligible runs while protecting baselines.
Applying a stale or changed preview is rejected. There is no automatic hard deletion.
Bundle export policies do not prevent copying records already visible to a reader.

Containers and CI files are provided but execution is unverified on this machine.
Run one application process per database; PostgreSQL does not add distributed job
coordination. No claim of production hardening, tenant-grade isolation or live security assessment
is made by this local release.
