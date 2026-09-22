"""Dependency assessment and opt-in, reviewed SQLite run deletion."""

import hashlib
import json
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import Field

from aegis.artifact_lifecycle import ArtifactLifecycle
from aegis.backup import restore, snapshot
from aegis.policy import Model
from aegis.recovery_retention import RecoveryRetention
from aegis.repository import Repository
from aegis.store import Store, TransactionStore


class DeletionPreview(Model):
    resource: Literal["runs", "artifacts"] = "runs"
    grace_days: int = Field(default=30, ge=1, le=36500)


class ApproveDeletion(Model):
    resource: Literal["runs", "artifacts"] = "runs"
    fingerprint: str = Field(pattern="^[a-f0-9]{64}$")


class ApplyDeletion(Model):
    token: str = Field(min_length=32, max_length=100)
    confirmation: str = Field(max_length=100)


def checksum(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class DeletionAssessment:
    def __init__(self, store: Store) -> None:
        self.store = store

    def preview(
        self, workspace: UUID, grace_days: int, *, transaction: bool = True
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        identity = str(workspace)
        with self.store.connection() as db:
            # Serialize with policy/reference writers, including PostgreSQL's advisory lock.
            if transaction:
                db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM workspaces WHERE id=?", (identity,)).fetchone():
                raise ValueError("Workspace not found")
            rows = db.execute(
                "SELECT r.id,r.evidence_id,r.payload,a.archived_at FROM runs r "
                "JOIN archived_runs a ON a.run_id=r.id WHERE r.workspace_id=? "
                "ORDER BY a.archived_at,r.id LIMIT 101",
                (identity,),
            ).fetchall()
            # Fail closed if an exhaustive reference inventory exceeds this bounded assessment.
            references: list[tuple[str, str, str]] = []
            for table, key in (
                ("workflow_records", "identity"),
                ("artifacts", "id"),
                ("jobs", "id"),
                ("runs", "id"),
            ):
                records = db.execute(
                    f"SELECT {key},payload FROM {table} WHERE workspace_id=? LIMIT 5001",
                    (identity,),
                ).fetchall()
                if len(records) > 5000:
                    raise ValueError("Reference inventory exceeds assessment limit")
                references.extend((table, str(key_value), raw) for key_value, raw in records)
            if sum(len(raw.encode()) for _, _, raw in references) > 10 * 1024 * 1024:
                raise ValueError("Reference inventory exceeds assessment limit")
            baselines = db.execute(
                "SELECT name,run_id FROM baselines WHERE workspace_id=? ORDER BY name",
                (identity,),
            ).fetchall()
            active = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE workspace_id=? AND status IN ('queued','running')",
                (identity,),
            ).fetchone()[0]
            holds = db.execute(
                "SELECT identity FROM workflow_records WHERE workspace_id=? "
                "AND kind IN ('hold','legal_hold','operational_hold')",
                (identity,),
            ).fetchall()
            workflow_history = db.execute(
                "SELECT kind,payload FROM workflow_records WHERE workspace_id=? "
                "AND kind IN ('triage','coverage','item')",
                (identity,),
            ).fetchall()
            linked_history = any(
                kind in ("triage", "coverage") or json.loads(raw).get("finding_id")
                for kind, raw in workflow_history
            )
            controls = []
            for table in ("scopes", "workspace_policies"):
                control = db.execute(
                    f"SELECT payload FROM {table} WHERE workspace_id=?",
                    (identity,),
                ).fetchone()
                controls.append(checksum(control[0]) if control else None)
            result = []
            for run_id, evidence_id, raw, archived_at in rows[:100]:
                archived = datetime.fromisoformat(archived_at)
                if archived.tzinfo is None:
                    archived = archived.replace(tzinfo=UTC)
                reasons = []
                if archived > now - timedelta(days=grace_days):
                    reasons.append("archive_grace_period")
                if any(row[1] == run_id for row in baselines):
                    reasons.append("baseline")
                if active:
                    reasons.append("active_workspace_jobs")
                if holds:
                    reasons.append("workspace_hold")
                if linked_history:
                    # Grouped finding IDs need not contain a run UUID. Preserve the whole
                    # assessment history until precise shared-history deletion is supported.
                    reasons.append("linked_workflow_history")
                links = []
                for table, record_id, payload in references:
                    if table == "runs" and record_id == run_id:
                        continue
                    # Conservative text matching also protects IDs embedded in finding keys.
                    if run_id in payload or evidence_id in payload:
                        links.append({"table": table, "id": record_id})
                if links:
                    reasons.append("referenced_record")
                evidence = db.execute(
                    "SELECT sha256,payload FROM evidence WHERE id=? AND workspace_id=?",
                    (evidence_id, identity),
                ).fetchone()
                if evidence is None or checksum(evidence[1]) != evidence[0]:
                    reasons.append("evidence_integrity")
                result.append(
                    {
                        "run_id": run_id,
                        "run_sha256": checksum(raw),
                        "evidence_id": evidence_id,
                        "evidence_sha256": evidence[0] if evidence else None,
                        "archived_at": archived_at,
                        "estimated_payload_bytes": len(raw.encode())
                        + (len(evidence[1].encode()) if evidence else 0),
                        "eligible": not reasons,
                        "blockers": reasons,
                        "references": links,
                    }
                )
            state = {
                "workspace_id": identity,
                "grace_days": grace_days,
                "records": result,
                "controls": controls,
                "baselines": list(baselines),
                "references": sorted((t, k, checksum(p)) for t, k, p in references),
                "holds": sorted(row[0] for row in holds),
                "active_jobs": active,
            }
            return {
                "workspace_id": identity,
                "as_of": now.isoformat(),
                "expires_at": (now + timedelta(minutes=5)).isoformat(),
                "fingerprint": checksum(json.dumps(state, sort_keys=True, default=list)),
                "proposed_grace_days": grace_days,
                "records": result,
                "more": len(rows) > 100,
                "eligible_count": sum(r["eligible"] for r in result),
                "apply_enabled": False,
                "notice": "Assessment only. No records are deleted. Grace is proposed, not saved. "
                "Decisions, jobs, audit history, artifacts, backups and exports are retained. "
                "A fingerprint is not authorization to delete.",
            }


class DeletionManager:
    def __init__(
        self,
        store: Store,
        grace_days: int | None = None,
        *,
        pg_bin: Path | None = None,
        recovery_dir: Path | None = None,
        recovery_retention_days: int | None = None,
    ) -> None:
        if grace_days is not None and not 1 <= grace_days <= 36500:
            raise ValueError("Deletion grace must be 1-36500 days")
        self.store = store
        self.grace_days = grace_days
        self.pg_bin = pg_bin
        self.recovery_dir = recovery_dir
        self.lock = threading.Lock()
        self.approvals: dict[str, dict[str, Any]] = {}
        self.retention = RecoveryRetention(self, recovery_retention_days)

    @property
    def enabled(self) -> bool:
        return self.grace_days is not None and (
            not self.store.database_url
            or (self.pg_bin is not None and self.recovery_dir is not None)
        )

    def preview(self, workspace: UUID, proposed: int, resource: str = "runs") -> dict[str, Any]:
        assessor = ArtifactLifecycle if resource == "artifacts" else DeletionAssessment
        result = assessor(self.store).preview(workspace, self.grace_days or proposed)
        result["resource"] = resource
        result["apply_enabled"] = self.enabled
        result["configured_grace_days"] = self.grace_days
        if self.enabled:
            result["notice"] = (
                "Review this exact selection before preparing deletion. Preparation verifies a "
                "backup and restore; deletion requires a separate typed confirmation. "
                "Backups, exports, decisions, jobs, audit history "
                "and non-selected records are retained."
            )
        return result

    def prepare(
        self, workspace: UUID, actor: str, fingerprint: str, resource: str = "runs"
    ) -> dict[str, Any]:
        if not self.enabled or self.grace_days is None:
            raise ValueError("Deletion is disabled; configure deletion grace and recovery first")
        with self.lock:
            self.approvals = {
                k: v for k, v in self.approvals.items() if v["deadline"] > time.monotonic()
            }
            if len(self.approvals) >= 32:
                raise ValueError("Too many pending deletion reviews")
            preview = self.preview(workspace, self.grace_days, resource)
            if preview["fingerprint"] != fingerprint or not preview["eligible_count"]:
                raise ValueError("Selection changed or no eligible records")
            # A real snapshot/restore exercise, not an operator's unverified attestation.
            if self.store.database_url:
                from aegis.pg_recovery import verify_recovery

                assert self.pg_bin is not None and self.recovery_dir is not None
                backup = verify_recovery(self.store.database_url, self.recovery_dir, self.pg_bin)
            else:
                directory = self.store.path.parent / "deletion-recovery"
                directory.mkdir(exist_ok=True)
                identity = uuid4().hex
                backup = snapshot(self.store.path, directory / (identity + ".backup.db"))
                restore(
                    directory / (identity + ".backup.db"),
                    directory / (identity + ".restored.db"),
                    str(backup["sha256"]),
                )
            recovery_files = [Path(str(backup["path"]))]
            if not self.store.database_url:
                recovery_files.append(directory / (identity + ".restored.db"))
            self.retention.register(recovery_files, str(backup["sha256"]), actor)
            token = secrets.token_urlsafe(32)
            selected = [r for r in preview["records"] if r["eligible"]]
            phrase = f"DELETE {len(selected)} ARCHIVED {resource.upper()}"
            self.approvals[token] = {
                "workspace": str(workspace),
                "resource": resource,
                "actor": actor,
                "fingerprint": fingerprint,
                "deadline": time.monotonic() + 300,
                "grace": self.grace_days,
                "backup_sha256": backup["sha256"],
                "records": selected,
                "phrase": phrase,
            }
            return {
                "token": token,
                "confirmation": phrase,
                "records": selected,
                "expires_in_seconds": 300,
                "backup_sha256": backup["sha256"],
                "notice": "Permanent deletion removes this reviewed selection. "
                "Recovery copies remain on disk; restoring them reintroduces deleted data.",
            }

    def apply(self, workspace: UUID, actor: str, body: ApplyDeletion) -> dict[str, Any]:
        with self.lock:
            approval = self.approvals.get(body.token)
            if (
                not self.enabled
                or approval is None
                or approval["deadline"] <= time.monotonic()
                or approval["workspace"] != str(workspace)
                or approval["actor"] != actor
                or approval["grace"] != self.grace_days
                or body.confirmation != approval["phrase"]
            ):
                raise ValueError("Deletion approval expired or confirmation does not match")
            with self.store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                assessor = (
                    ArtifactLifecycle if approval["resource"] == "artifacts" else DeletionAssessment
                )
                preview = assessor(TransactionStore(db)).preview(
                    workspace, approval["grace"], transaction=False
                )
                if preview["fingerprint"] != approval["fingerprint"]:
                    raise ValueError("Deletion selection or dependencies changed; review again")
                manifest = {
                    "workspace_id": str(workspace),
                    "actor": actor,
                    "deleted_at": datetime.now(UTC).isoformat(),
                    "backup_sha256": approval["backup_sha256"],
                    "records": [
                        {
                            k: r[k]
                            for k in (
                                ("artifact_id", "artifact_sha256")
                                if approval["resource"] == "artifacts"
                                else ("run_id", "run_sha256", "evidence_id", "evidence_sha256")
                            )
                        }
                        for r in approval["records"]
                    ],
                }
                tombstone = {
                    "manifest": manifest,
                    "sha256": checksum(json.dumps(manifest, sort_keys=True)),
                }
                tombstone_id = str(uuid4())
                for record in approval["records"]:
                    if approval["resource"] == "artifacts":
                        db.execute(
                            "DELETE FROM workflow_records WHERE workspace_id=? "
                            "AND kind='artifact_archive' AND identity=?",
                            (str(workspace), record["artifact_id"]),
                        )
                        db.execute(
                            "DELETE FROM artifacts WHERE id=? AND workspace_id=?",
                            (record["artifact_id"], str(workspace)),
                        )
                        continue
                    db.execute("DELETE FROM archived_runs WHERE run_id=?", (record["run_id"],))
                    db.execute(
                        "DELETE FROM runs WHERE id=? AND workspace_id=?",
                        (record["run_id"], str(workspace)),
                    )
                    db.execute(
                        "DELETE FROM evidence WHERE id=? AND workspace_id=?",
                        (record["evidence_id"], str(workspace)),
                    )
                db.execute(
                    "INSERT INTO workflow_records VALUES (?,?,?,?)",
                    (
                        str(workspace),
                        "deletion_tombstone",
                        tombstone_id,
                        json.dumps(tombstone, sort_keys=True),
                    ),
                )
                Repository(TransactionStore(db)).audit(
                    workspace,
                    "deletion.applied",
                    {
                        "actor": actor,
                        "tombstone_id": tombstone_id,
                        "manifest_sha256": tombstone["sha256"],
                        "count": len(approval["records"]),
                    },
                )
            del self.approvals[body.token]
            return {
                "deleted_" + approval["resource"]: len(approval["records"]),
                "tombstone_id": tombstone_id,
            }
