# Local platform validation

Verified on Windows / Python 3.12.14 on 2026-09-13 local time.

- Automated suite: **88 passed** in 4.10 seconds. Two upstream Starlette/AnyIO
  deprecation warnings remain visible; no warnings were suppressed.
- Ruff lint and format: passed. Mypy: passed for 20 source files.
- Frontend JavaScript syntax and Prettier formatting: passed.
- Wheel and source distribution: built successfully.
- Clean environment: installed the wheel, imported API/gate modules and verified
  packaged dashboard assets. Started the installed application on loopback port
  8767; HTTP health, HTML and current JavaScript checks passed.
- Browser: authenticated to the local app, inspected the synthetic regression,
  compared it with its secure baseline, evaluated a failing gate with exit 1,
  submitted a notebook workflow and inspected the persisted artifact with its
  SHA-256, adapter version, job and scope decision references.
- API tests also verify archive/restore, cross-workspace access checks, gate audit,
  authentication, size/origin/host limits and all nine notebook modes.
- Job tests include cancellation, recovery, queue saturation and scope revocation
  during execution. Adapter tests cover malformed inputs and unknown outcomes.

The visible example has three synthetic runs (PASS, FAIL, PASS) and one test note.
This verifies offline record handling, not live vulnerability detection.

Docker is unavailable locally; container execution is unverified. GitHub Actions
configuration has not run remotely. PostgreSQL, multiuser administration and the
complete blueprint are not implemented. See `roadmap.md` and `security.md`.

## September 14 continuation

API pagination added; 113 tests pass, Ruff lint/format and Mypy pass.
See `pagination.md` for coverage and limitations. Packaging and browser results
above remain the September 13 checkpoint; they were not rerun for this API change.

## September 14 platform continuation

- SQLite suite: **141 passed**, with one PostgreSQL-only transfer test skipped in
  that run; the transfer test passed separately against PostgreSQL 17.11.
- PostgreSQL: all 46 API tests at the storage checkpoint passed; three follow-up
  cases covered SARIF, numeric threshold evidence and fixture/report/retention after
  the adapter additions. Together these exercise all 48 current API cases.
- Browser: four Playwright flows passed in Chrome, covering history pagination,
  retention/export, credential issuance/revocation and SARIF import/inspection.
- Ruff lint/format, Mypy (26 source files), frontend syntax/format and wheel/sdist
  builds passed. Two upstream deprecation warnings remain.
- Backup/restore round-trip and corrupt-evidence rejection passed. The existing
  app database was backed up before its schema upgrade; its old observations remain.
- Existing app: authenticated sign-in, dashboard and Data policies view checked
  against the real local SQLite data. App available on port 8766. Test PostgreSQL
  ran in a separate loopback-only cluster, which was stopped after verification.

New operational instructions are in `operations.md`; SARIF limits are in
`sarif-import.md`. Docker and remote CI remain unexecuted. Production load,
identity-provider integration, distributed workers and full blueprint acceptance
remain incomplete. No entire-blueprint completion percentage is claimed.
