"""Application boundary for offline observation import and reporting."""

import html
import json
import re
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from aegis.assurance import Fixture, Run, evaluate
from aegis.policy import Scope, authorize
from aegis.store import Store


def redact(text: str) -> str:
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", text)
    return re.sub(
        r"(?i)(password|token|api[_-]?key|authorization|cookie)(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[REDACTED]",
        text,
    )


def read_json(path: Path) -> object:
    if path.stat().st_size > 1_048_576:
        raise ValueError("Input exceeds 1 MiB limit")
    return json.loads(path.read_text(encoding="utf-8"))


def sanitize_metadata(value: object) -> object:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [sanitize_metadata(item) for item in value]
    if isinstance(value, dict):
        return {redact(str(key)): sanitize_metadata(item) for key, item in value.items()}
    return value


def import_fixture(
    store: Store,
    workspace_id: UUID,
    scope: Scope,
    fixture: Fixture,
    *,
    source_kind: Literal["fixture_observation", "junit_observation"] = "fixture_observation",
    source_metadata: dict[str, object] | None = None,
) -> Run:
    workspace = store.workspace(workspace_id)
    decision = authorize(workspace, scope, fixture.target, "fixture.evaluate")
    store.record_decision(decision)
    if not decision.allowed:
        raise ValueError(f"Policy denied: {decision.reason}")
    # Sanitize before both evaluation and evidence persistence; originals are not retained.
    sanitized = fixture.model_copy(
        update={
            "checks": tuple(
                check.model_copy(update={"title": redact(check.title)}) for check in fixture.checks
            ),
            "target_version": redact(fixture.target_version),
            "policy_version": redact(fixture.policy_version),
            "identity_set": redact(fixture.identity_set),
        }
    )
    evidence_id = uuid4()
    run = evaluate(sanitized, workspace.id, decision.id, evidence_id)
    evidence: dict[str, object] = {
        "kind": source_kind,
        "source": "operator_supplied",
        "tool": "aegis-offline-evaluator",
        "tool_version": "0.1.0",
        "sanitization_status": "common_patterns_redacted",
        "timestamp": run.created_at.isoformat(),
        "run_id": str(run.id),
        "fixture": sanitized.model_dump(mode="json"),
    }
    if source_metadata is not None:
        evidence["adapter_metadata"] = sanitize_metadata(source_metadata)
    store.save_run(run, evidence)
    return run


def render_report(run: Run, evidence: dict[str, object]) -> str:
    rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape(str(v))}</td>"
            for v in (
                a.check_id,
                a.title,
                a.outcome,
                a.expected,
                a.observed,
            )
        )
        + "</tr>"
        for a in run.assertions
    )
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>AEGIS assurance report</title>"
        "<style>body{font:16px system-ui;max-width:1000px;margin:32px auto;padding:0 16px}"
        "table{border-collapse:collapse;width:100%}td,th{padding:10px;text-align:left;"
        "border-bottom:1px solid #bbb;overflow-wrap:anywhere}pre{white-space:pre-wrap;"
        "overflow-wrap:anywhere}</style><h1>AEGIS assurance report</h1>"
        f"<p>Run {run.id} · Target {html.escape(run.target)}</p>"
        "<p>Operator-supplied fixture observations. No live security testing performed.</p>"
        "<table><thead><tr><th>Check</th><th>Title</th><th>Outcome</th>"
        "<th>Expected</th><th>Observed</th></tr></thead><tbody>" + rows + "</tbody></table>"
        f"<h2>Evidence</h2><p>Evidence ID {run.evidence_id} · Decision {run.decision_id}</p>"
        "<pre>" + html.escape(json.dumps(evidence, indent=2)) + "</pre></html>"
    )
