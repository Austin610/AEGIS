"""Evidence-linked triage and requirements assessments for offline observations."""

import hashlib
import json
from contextlib import nullcontext
from datetime import UTC, datetime
from html import escape
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import Field

from aegis.assurance import Run, digest
from aegis.policy import Model
from aegis.repository import Repository, current_actor
from aegis.service import sanitize_metadata


class Triage(Model):
    revision: int = Field(ge=0)
    status: Literal["Open", "Triaged", "Fixing", "Retest", "Closed"] = "Open"
    owner: str = Field(default="", max_length=200)
    priority: Literal["P0", "P1", "P2", "P3"] = "P2"
    notes: str = Field(default="", max_length=10000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    closure_run_id: UUID | None = None
    closure_check_id: str | None = Field(default=None, max_length=100)


class Assessment(Model):
    revision: int = Field(ge=0)
    framework: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=50)
    control: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    status: Literal["Untested", "Passed", "Failed", "Needs review"] = "Untested"
    evidence_run_id: UUID | None = None
    rationale: str = Field(default="", max_length=10000)


class Workflow:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def snapshot(self, workspace_id: UUID, connection: Any = None) -> dict[str, Any]:
        if connection is None:
            self.repository.store.workspace(workspace_id)
        with (
            nullcontext(connection)
            if connection is not None
            else self.repository.store.connection()
        ) as db:
            if connection is None:
                db.execute("BEGIN")
            total = 0
            for table in ("runs", "evidence", "artifacts", "workflow_records"):
                count, size = db.execute(
                    f"SELECT COUNT(*), COALESCE(SUM(LENGTH(payload)),0) FROM {table} "
                    "WHERE workspace_id=?",
                    (str(workspace_id),),
                ).fetchone()
                total += size
                if count > 5000 or total > 10 * 1024 * 1024:
                    raise ValueError("Workflow exceeds bounded snapshot capacity")
            runs = db.execute(
                "SELECT r.payload,e.payload,e.sha256 FROM runs r JOIN evidence e "
                "ON e.id=r.evidence_id WHERE r.workspace_id=? ORDER BY r.rowid",
                (str(workspace_id),),
            ).fetchmany(5001)
            artifacts = db.execute(
                "SELECT id, payload, sha256, created_at FROM artifacts "
                "WHERE workspace_id=? AND kind=? ORDER BY rowid",
                (str(workspace_id), "sarif_report"),
            ).fetchmany(5001)
            records = db.execute(
                "SELECT kind, identity, payload FROM workflow_records WHERE workspace_id=?",
                (str(workspace_id),),
            ).fetchmany(5001)
        if max(len(runs), len(artifacts), len(records)) > 5000:
            raise ValueError("Workflow snapshot exceeds 5000 source records")
        items: dict[str, Any] = {}
        latest: dict[tuple[str, str, str], dict[str, str]] = {}
        contexts: dict[str, tuple[Run, str]] = {}
        for row in runs:
            if hashlib.sha256(row[1].encode()).hexdigest() != row[2]:
                raise ValueError("Evidence integrity check failed")
            run = Run.model_validate_json(row[0])
            evidence = json.loads(row[1])
            adapter = evidence.get("adapter_metadata", {}).get("adapter_id", "fixture")
            compatibility = digest([run.compatibility_hash, adapter, evidence["kind"]])
            contexts[str(run.id)] = (run, compatibility)
            for assertion in run.assertions:
                latest[(run.target, compatibility, assertion.check_id)] = {
                    "outcome": assertion.outcome,
                    "run_id": str(run.id),
                    "created_at": run.created_at.isoformat(),
                }
            for finding in run.findings:
                # Policy compatibility separates observations that cannot be retested together.
                key = digest(["fixture", finding.fingerprint, compatibility])
                item = items.setdefault(
                    key,
                    {
                        "id": key,
                        "source": "fixture",
                        "adapter": adapter,
                        "target": run.target,
                        "title": finding.title,
                        "severity": finding.severity,
                        "check_id": finding.check_id,
                        "compatibility": compatibility,
                        "first_seen": run.created_at.isoformat(),
                        "occurrences": [],
                        "verification": f"Supplied {adapter} observation; "
                        "not independently verified",
                    },
                )
                item["last_seen"] = run.created_at.isoformat()
                item["occurrences"].append(
                    {"run_id": str(run.id), "evidence_id": str(run.evidence_id)}
                )
        for artifact_id, payload, checksum, created_at in artifacts:
            if hashlib.sha256(payload.encode()).hexdigest() != checksum:
                raise ValueError("Artifact integrity check failed")
            data = json.loads(payload)
            for result in data.get("results", []):
                if result["state"] != "fail" or result["level"] == "none":
                    continue
                key = digest(
                    [
                        "sarif",
                        data["target"],
                        result["tool"],
                        result["rule_id"],
                        result["locations"],
                        result["message"],
                    ]
                )
                item = items.setdefault(
                    key,
                    {
                        "id": key,
                        "source": "sarif",
                        "target": data["target"],
                        "title": result["message"],
                        "severity": result["level"],
                        "check_id": result["rule_id"],
                        "occurrences": [],
                        "verification": "Imported tool observation; not independently verified",
                    },
                )
                occurrence = {"artifact_id": artifact_id}
                item.setdefault("first_seen", created_at)
                item["last_seen"] = created_at
                if occurrence not in item["occurrences"]:
                    item["occurrences"].append(occurrence)
        triages = {
            identity: json.loads(payload) for kind, identity, payload in records if kind == "triage"
        }
        coverage = [
            json.loads(payload) | {"id": identity}
            for kind, identity, payload in records
            if kind == "coverage"
        ]
        for key, item in items.items():
            item["triage"] = triages.get(key, Triage(revision=0).model_dump(mode="json"))
            item["retest"] = latest.get(
                (item["target"], item.get("compatibility", ""), item["check_id"]),
                {"outcome": "UNKNOWN"},
            )
            if item["source"] == "sarif":
                item["retest_candidates"] = [
                    {"run_id": str(run.id), "check_id": assertion.check_id}
                    for run, compatibility in contexts.values()
                    if run.target == item["target"]
                    and run.created_at.isoformat() >= item["last_seen"]
                    for assertion in run.assertions
                    if assertion.outcome == "PASS"
                    and latest[(run.target, compatibility, assertion.check_id)]["run_id"]
                    == str(run.id)
                ]
                selected = contexts.get(item["triage"].get("closure_run_id"))
                check_id = item["triage"].get("closure_check_id")
                if selected and selected[0].target == item["target"]:
                    candidate = latest.get((selected[0].target, selected[1], check_id))
                    if candidate and candidate["created_at"] >= item["last_seen"]:
                        item["retest"] = {**candidate, "basis": "Analyst-linked recorded retest"}
            item["trend"] = (
                "resolved observation"
                if item["retest"]["outcome"] == "PASS"
                else "recurring"
                if len(item["occurrences"]) > 1
                else "new"
            )
            item["closure_stale"] = (
                item["triage"]["status"] == "Closed" and item["retest"]["outcome"] != "PASS"
            )
        return {
            "findings": list(items.values()),
            "coverage": coverage,
            "generated_at": datetime.now(UTC).isoformat(),
            "limitations": "Archive status does not remove history. Missing SARIF results "
            "never establish a fix. Coverage is a human assessment, not certification.",
        }

    def save(
        self, workspace_id: UUID, kind: str, identity: str, value: Triage | Assessment
    ) -> dict[str, Any]:
        snapshot = self.snapshot(workspace_id)
        if isinstance(value, Triage):
            finding = next((f for f in snapshot["findings"] if f["id"] == identity), None)
            if finding is None:
                raise ValueError("Unknown finding")
            if value.status == "Closed":
                self.validate_closure(finding, value)
        elif value.evidence_run_id:
            self.repository.store.run(value.evidence_run_id, workspace_id)
        elif value.status in {"Passed", "Failed"}:
            raise ValueError("Assessed pass/fail requires a workspace evidence run")
        payload = value.model_dump(mode="json")
        for field in (
            "owner",
            "notes",
            "tags",
            "framework",
            "version",
            "control",
            "title",
            "rationale",
        ):
            if field in payload:
                payload[field] = sanitize_metadata(payload[field])
        payload["revision"] += 1
        payload["updated_at"] = datetime.now(UTC).isoformat()
        with self.repository.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if isinstance(value, Triage):
                current = next(
                    (f for f in self.snapshot(workspace_id, db)["findings"] if f["id"] == identity),
                    None,
                )
                if current is None:
                    raise ValueError("Finding no longer exists")
                if value.status == "Closed":
                    self.validate_closure(current, value)
            elif (
                value.evidence_run_id
                and not db.execute(
                    "SELECT 1 FROM runs WHERE id=? AND workspace_id=?",
                    (str(value.evidence_run_id), str(workspace_id)),
                ).fetchone()
            ):
                raise ValueError("Evidence run no longer exists")
            row = db.execute(
                "SELECT payload FROM workflow_records WHERE workspace_id=? "
                "AND kind=? AND identity=?",
                (str(workspace_id), kind, identity),
            ).fetchone()
            revision = json.loads(row[0])["revision"] if row else 0
            if revision != value.revision:
                raise ValueError("Record changed; reload before saving")
            db.execute(
                "INSERT INTO workflow_records(workspace_id,kind,identity,payload) "
                "VALUES (?,?,?,?) ON CONFLICT(workspace_id,kind,identity) "
                "DO UPDATE SET payload=excluded.payload",
                (str(workspace_id), kind, identity, json.dumps(payload)),
            )
            db.execute(
                "INSERT INTO audit_events(id,workspace_id,created_at,action,payload) "
                "VALUES (?,?,?,?,?)",
                (
                    str(uuid4()),
                    str(workspace_id),
                    payload["updated_at"],
                    "workflow." + kind,
                    json.dumps(
                        {"actor": current_actor.get(), "identity": identity, "record": payload}
                    ),
                ),
            )
        return payload

    @staticmethod
    def validate_closure(finding: dict[str, Any], value: Triage) -> None:
        if finding["source"] == "sarif":
            if (
                not value.notes.strip()
                or {"run_id": str(value.closure_run_id), "check_id": value.closure_check_id}
                not in finding["retest_candidates"]
            ):
                raise ValueError(
                    "SARIF closure needs an analyst-linked passing retest and rationale"
                )
        elif finding["retest"]["outcome"] != "PASS" or str(value.closure_run_id) != finding[
            "retest"
        ].get("run_id"):
            raise ValueError("Closure requires the latest compatible passing retest")

    def report(self, workspace_id: UUID) -> str:
        snapshot = self.snapshot(workspace_id)
        name = self.repository.store.workspace(workspace_id).name
        rows = "".join(
            "<tr>"
            + "".join(
                "<td>" + escape(str(v)) + "</td>"
                for v in (
                    f["title"],
                    f["source"],
                    f["severity"],
                    f["triage"]["status"],
                    f["triage"]["owner"],
                    f["trend"],
                    json.dumps(f["occurrences"]),
                )
            )
            + "</tr>"
            for f in snapshot["findings"]
        )
        coverage = "".join(
            "<li>"
            + escape(
                f"{c['framework']} {c['version']} {c['control']} {c['title']}: "
                f"{c['status']} — {c['rationale']}"
            )
            + "</li>"
            for c in snapshot["coverage"]
        )
        return (
            "<!doctype html><html lang='en'><meta charset='utf-8'><title>AEGIS report</title>"
            "<style>body{font:15px/1.5 system-ui;color:#192735;max-width:1200px;margin:40px auto;"
            "padding:24px}h1{border-bottom:3px solid #278268;padding-bottom:16px}"
            "table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:12px;"
            "border:1px solid #cdd6dc;text-align:left;vertical-align:top;overflow-wrap:anywhere}"
            "th{background:#edf5f2}li{margin:12px 0}@media print{body{margin:0;padding:0}"
            "tr{break-inside:avoid}thead{display:table-header-group}}</style>"
            f"<h1>{escape(name)} — assurance report</h1><p>{escape(snapshot['generated_at'])}</p>"
            f"<p>{len(snapshot['findings'])} distinct historical findings; "
            f"{len(snapshot['coverage'])} assessed requirements.</p>"
            f"<p>{escape(snapshot['limitations'])}</p><table><thead><tr>"
            "<th>Finding</th><th>Source</th><th>Severity</th><th>Status</th><th>Owner</th>"
            f"<th>Trend</th><th>Evidence references</th></tr></thead><tbody>{rows}</tbody></table>"
            f"<h2>Requirements coverage</h2><ul>{coverage}</ul></html>"
        )
