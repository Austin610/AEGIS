"""Reproducible offline demonstration, with no network calls or external tools."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aegis.assurance import Fixture, compare
from aegis.policy import Scope, Workspace
from aegis.service import import_fixture, render_report
from aegis.store import Store


def run_demo(output: Path) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=False)
    store = Store(output / "aegis.db")
    workspace = Workspace(name="Offline regression demonstration")
    store.add_workspace(workspace)
    now = datetime.now(UTC)
    scope = Scope(
        workspace_id=workspace.id,
        allowed_targets=("fixture://documents/read",),
        capabilities=("fixture.evaluate",),
        starts_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
    )
    (output / "scope.json").write_text(scope.model_dump_json(indent=2), encoding="utf-8")
    runs = []
    for version, observation in (("secure", "deny"), ("regressed", "allow"), ("fixed", "deny")):
        fixture = Fixture.model_validate(
            {
                "target": "fixture://documents/read",
                "target_version": version,
                "policy_version": "ownership-v1",
                "identity_set": "alice-bob-v1",
                "checks": [
                    {
                        "id": "non-owner-read",
                        "title": "Non-owner access is denied",
                        "expected": "deny",
                        "observed": observation,
                        "severity": "high",
                    }
                ],
            }
        )
        (output / f"{version}-fixture.json").write_text(
            fixture.model_dump_json(indent=2), encoding="utf-8"
        )
        run = import_fixture(store, workspace.id, scope, fixture)
        runs.append(run)
        evidence = store.evidence(run.evidence_id, workspace.id)
        (output / f"{version}.html").write_text(render_report(run, evidence), encoding="utf-8")
        (output / f"{version}.json").write_text(run.model_dump_json(indent=2), encoding="utf-8")
    store.baseline(workspace.id, "secure", runs[0].id)
    summary: dict[str, object] = {
        "workspace_id": str(workspace.id),
        "source": "synthetic observations; no real vulnerability reproduced",
        "secure_to_regressed": compare(runs[0], runs[1]),
        "regressed_to_fixed": compare(runs[1], runs[2]),
        "run_ids": [str(run.id) for run in runs],
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
