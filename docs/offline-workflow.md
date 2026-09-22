# Offline assurance workflow

The current implementation evaluates observations supplied in a fixture JSON file.
It never sends a request, reproduces a vulnerability or infers access from HTTP status.
The included demo creates passing, failing and fixed observations to exercise the
storage and reporting pipeline.

## Commands

Run `aegis assurance --help` for all commands. Each command accepts `--database`
to select local SQLite storage, defaulting to `.aegis/aegis.db`.

```text
aegis assurance workspace-create NAME --mode appsec
aegis assurance import-fixture WORKSPACE_UUID FIXTURE_JSON SCOPE_JSON
aegis assurance import-junit WORKSPACE_UUID RESULTS_XML SCOPE_JSON --target fixture://qa/suite --revision COMMIT
aegis assurance baseline-create WORKSPACE_UUID NAME RUN_UUID
aegis assurance compare WORKSPACE_UUID BASELINE_NAME RUN_UUID
aegis assurance report WORKSPACE_UUID RUN_UUID OUTPUT.html
aegis assurance report WORKSPACE_UUID RUN_UUID OUTPUT.json --format json
aegis assurance demo NEW_OUTPUT_DIRECTORY
```

Workspace creation prints its UUID. Fixture import prints a run JSON object including
its UUID. Supply those identifiers to subsequent commands. JSON scope dates require
time zones; the start is inclusive and the expiry is exclusive. The demo emits example
inputs. Its scope expires after one hour. Only exact lowercase `fixture://` identifiers
are accepted; wildcards, percent encoding, credentials, query strings and traversal
segments are rejected. Exclusions override inclusions.

Supported workspace modes are appsec, QA, research and forensics. Forensics cannot
evaluate fixtures. The declared evidence-import capability is reserved; a standalone
arbitrary-artifact ingestion command is not implemented.

JUnit import requires a QA workspace. It accepts UTF-8 XML up to 1 MiB with up to
1000 named cases. DTD/entity declarations, contradictory outcomes, duplicate case
identities and empty suites are rejected. Failure is FAIL, error is ERROR and skipped
is UNKNOWN. Failure bodies, properties and captured output are omitted. Evidence
records that its observations came from JUnit. `--suite-version` defaults to v1;
change it when the suite's interpretation changes. The original XML is not stored.

Import exit codes: 0 means all assertions pass, 1 means failed assertions, and 2 means
an operation error or at least one UNKNOWN/ERROR observation. A report is still available
for stored failing or uncertain runs. Compare prints classifications; it is informational
and does not yet apply a configurable CI gate.

## Evidence and comparison

Failed assertions produce findings linked to evidence, with confidence explicitly marked
as fixture observation. Evidence is a sanitized JSON derivative with tool version, run
identity and timestamp; originals are not retained. Common token/password patterns in
free-text titles and metadata are redacted before storage. This is not a guarantee that
arbitrary sensitive text is detectable: fixtures should contain synthetic data only.
Reports HTML-escape all supplied content and refuse to overwrite existing output files.

Comparisons require the same workspace, target, policy version, identity set and check
definitions. Target version is allowed to change intentionally so regressions can be
compared between revisions. PASS to FAIL is new_regression; FAIL to PASS is fixed.
Ambiguous or error transitions are uncertain. Named baselines cannot be replaced by
normal application commands. Run/evidence inserts roll back together on errors.

## Remaining work

Network scope enforcement, real application testing, SQLAlchemy/Alembic migrations,
PostgreSQL, local API access control, worker scheduling, additional QA adapters, general
artifact ingestion, retention controls and research datasets remain unimplemented.
The demo and reports are an offline core prototype, not completion of the whole blueprint.
