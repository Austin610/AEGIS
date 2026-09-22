# Offline slice validation

Validated on Windows, Python 3.12.14, September 12, 2026.

| Command | Result |
| --- | --- |
| `.\.venv\Scripts\python.exe -m pytest --junitxml=.aegis/qa-selftest.xml` | 48 passed in 0.97 seconds |
| `.\.venv\Scripts\python.exe -m ruff check src tests` | Passed |
| `.\.venv\Scripts\python.exe -m mypy` | Passed, 12 source files |
| `.\.venv\Scripts\aegis.exe assurance demo .aegis/demo` | Regression and fixed classifications produced |
| `.\.venv\Scripts\aegis.exe assurance demo .aegis/demo-verified` | Final demo after provenance checks passed |
| `.\.venv\Scripts\python.exe -m ruff format --check src tests` | Passed |
| `.\.venv\Scripts\python.exe -m build` | Source distribution and wheel built |

New checks cover fixture target ambiguity, scope denial precedence, mode isolation,
timezone validation, duplicate assertions, persisted runs, cross-workspace access,
immutable baseline names, denied-import audit events, uncertainty, incompatible
baselines, evidence tampering, HTML escaping, common secret patterns, CLI exit codes,
output overwrite protection, transaction rollback and unsupported schema versions.
The demo test prevents socket creation and DNS lookup to verify offline execution.

Additional tests cover JUnit outcome mapping, XML declaration restrictions, evidence
provenance and CLI ingestion; SQLite migration preservation, idempotency and rollback.
The emitted self-test JUnit file was ingested through the application service and
produced an HTML report with 48 passing observations and zero findings.

All changes are confined to the AEGIS subdirectory and the task's progress visualization.
Docker remains unavailable. No external target testing was performed.
