"""Reversible artifact archive markers and conservative dependency assessment."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from aegis.repository import Repository
from aegis.store import Store, TransactionStore


def digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class ArtifactLifecycle:
    def __init__(self, store: Store) -> None:
        self.store = store

    def archive(self, workspace: UUID, artifact: UUID, archived: bool) -> None:
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(
                "SELECT 1 FROM artifacts WHERE id=? AND workspace_id=?",
                (str(artifact), str(workspace)),
            ).fetchone():
                raise ValueError("Artifact not found")
            if archived:
                db.execute(
                    "INSERT INTO workflow_records(workspace_id,kind,identity,payload) "
                    "VALUES (?,'artifact_archive',?,?) ON CONFLICT DO NOTHING",
                    (
                        str(workspace),
                        str(artifact),
                        json.dumps({"archived_at": datetime.now(UTC).isoformat()}),
                    ),
                )
            else:
                db.execute(
                    "DELETE FROM workflow_records WHERE workspace_id=? "
                    "AND kind='artifact_archive' AND identity=?",
                    (str(workspace), str(artifact)),
                )
            Repository(TransactionStore(db)).audit(
                workspace,
                "artifact.archived" if archived else "artifact.restored",
                {"artifact_id": str(artifact)},
            )

    def preview(
        self, workspace: UUID, grace_days: int, *, transaction: bool = True
    ) -> dict[str, Any]:
        identity = str(workspace)
        now = datetime.now(UTC)
        with self.store.connection() as db:
            if transaction:
                db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM workspaces WHERE id=?", (identity,)).fetchone():
                raise ValueError("Workspace not found")
            inventory = []
            budget = 10 * 1024 * 1024
            for table in (
                "artifacts",
                "runs",
                "evidence",
                "decisions",
                "jobs",
                "scopes",
                "workspace_policies",
                "workflow_records",
            ):
                key = (
                    "identity"
                    if table == "workflow_records"
                    else ("workspace_id" if table in ("scopes", "workspace_policies") else "id")
                )
                rows = db.execute(
                    f"SELECT {key},payload FROM {table} WHERE workspace_id=? LIMIT 5001",
                    (identity,),
                ).fetchall()
                if len(rows) > 5000:
                    raise ValueError("Artifact dependency inventory exceeds limit")
                for record_id, raw in rows:
                    budget -= len(raw.encode())
                    if budget < 0:
                        raise ValueError("Artifact dependency inventory exceeds limit")
                    inventory.append((table, record_id, raw))
            protected = db.execute(
                "SELECT kind,payload FROM workflow_records WHERE workspace_id=? "
                "AND kind IN ('hold','legal_hold','operational_hold','triage','coverage','item')",
                (identity,),
            ).fetchall()
            linked = any(
                kind in ("triage", "coverage")
                or (kind == "item" and json.loads(raw).get("finding_id"))
                for kind, raw in protected
            )
            held = any(kind.endswith("hold") for kind, _ in protected)
            active = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE workspace_id=? AND status IN ('queued','running')",
                (identity,),
            ).fetchone()[0]
            rows = db.execute(
                "SELECT a.id,a.sha256,a.payload,w.payload FROM artifacts a "
                "JOIN workflow_records w ON w.workspace_id=a.workspace_id "
                "AND w.identity=a.id AND w.kind='artifact_archive' "
                "WHERE a.workspace_id=? ORDER BY a.id LIMIT 101",
                (identity,),
            ).fetchall()
            result = []
            for artifact_id, expected, raw, marker in rows[:100]:
                archived = datetime.fromisoformat(json.loads(marker)["archived_at"])
                if archived.tzinfo is None:
                    archived = archived.replace(tzinfo=UTC)
                reasons = []
                if archived > now - timedelta(days=grace_days):
                    reasons.append("archive_grace_period")
                if linked:
                    reasons.append("linked_workflow_history")
                if held:
                    reasons.append("workspace_hold")
                if active:
                    reasons.append("active_workspace_jobs")
                if digest(raw) != expected:
                    reasons.append("artifact_integrity")
                links = [
                    {"table": t, "id": key}
                    for t, key, payload in inventory
                    if not (t == "artifacts" and key == artifact_id) and artifact_id in payload
                ]
                if links:
                    reasons.append("referenced_record")
                result.append(
                    {
                        "artifact_id": artifact_id,
                        "artifact_sha256": expected,
                        "archived_at": archived.isoformat(),
                        "eligible": not reasons,
                        "blockers": reasons,
                        "references": links,
                        "estimated_payload_bytes": len(raw.encode()),
                    }
                )
            state = {
                "workspace_id": identity,
                "resource": "artifacts",
                "grace_days": grace_days,
                "records": result,
                "active_jobs": active,
                "inventory": sorted((t, k, digest(raw)) for t, k, raw in inventory),
            }
            return {
                "workspace_id": identity,
                "resource": "artifacts",
                "as_of": now.isoformat(),
                "expires_at": (now + timedelta(minutes=5)).isoformat(),
                "fingerprint": digest(json.dumps(state, sort_keys=True)),
                "records": result,
                "proposed_grace_days": grace_days,
                "more": len(rows) > 100,
                "eligible_count": sum(r["eligible"] for r in result),
                "apply_enabled": False,
                "notice": "Assessment only. Artifacts stay visible while archived to preserve "
                "findings history. Backups, exports, jobs and audit records are retained.",
            }
