# Development

Use Python 3.12+ and install `.[dev]` into a virtual environment. For frontend
formatting, run `npm ci --ignore-scripts` with Node.js/npm installed. No frontend
bundler or runtime CDN is required; the app serves its packaged assets directly.

Run `scripts/verify.ps1` on Windows. Equivalent commands:

```text
python -m pytest
python -m ruff check src tests
python -m ruff format --check src tests
python -m mypy
npm run check
python -m build
```

Tests use temporary databases and synthetic inputs. They cover policy boundaries,
transactional migrations, integrity/provenance, adapter validation, authentication,
workspace isolation, jobs, cancellation/recovery, scope revocation and gate decisions.
TestClient emits upstream Starlette/AnyIO deprecation warnings in the tested environment.

`requirements-dev.lock` is a snapshot of installed development package versions;
CI uses it as constraints while installing the project. The GitHub Actions workflow
assumes this directory is the repository root. It has been prepared locally, not
executed on GitHub. No repository has been published.

Add adapters through the static registry and typed contract. Keep work bounded,
validate payloads, preserve provenance and retain explicit unknown/error outcomes.
Cooperative timeouts do not isolate arbitrary or hostile Python extensions.

Local state, tokens, build artifacts, virtual environments and node_modules are
ignored. Do not commit real evidence or access tokens. Restore from a backup taken
while the server is stopped if SQLite recovery is needed; distributed storage and
online backup orchestration are not implemented.
