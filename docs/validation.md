# M0 validation

Validated on Windows with Python 3.12.14 on 2026-09-12. Commands ran from the
`aegis` directory. Package versions are in `validation-environment.txt`.

| Command | Result |
| --- | --- |
| `.\.venv\Scripts\python.exe -m pip install -e '.[dev]'` | Installed successfully |
| `.\.venv\Scripts\python.exe -m pytest` | 12 passed in 0.43 seconds |
| `.\.venv\Scripts\python.exe -m ruff check .` | All checks passed |
| `.\.venv\Scripts\python.exe -m ruff format --check .` | Passed |
| `.\.venv\Scripts\python.exe -m mypy` | No issues in 5 source files |
| `.\.venv\Scripts\aegis.exe --version` | AEGIS 0.1.0 |
| `.\.venv\Scripts\aegis.exe doctor` | Valid configuration; Git present; Docker absent |
| `.\.venv\Scripts\python.exe -m build` | Source distribution and wheel built |

The first installation attempt preceded README creation and failed metadata
validation. Installation succeeded after the README was added. Ruff initially
reported two long lines; formatting corrected them before the passing checks.

Coverage includes config precedence, malformed/unknown configuration, file limits,
missing files, secret omission, CLI error exit codes, nested run contexts, exception
context cleanup and repeated logger setup. No claim of measured coverage percentage.

Docker/Compose execution was not tested because Docker is not on PATH. Database,
API, policy, evidence, migrations and target workflows are not implemented. No
network testing was performed. Dependency installation accessed package indexes.
No Git commit or repository-wide hook installation was performed.
