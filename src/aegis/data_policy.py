"""Reviewable run archiving and bounded, integrity-checked workspace exports."""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import Field

from aegis.assurance import Run, digest
from aegis.policy import Model
from aegis.store import Store

MAX_EXPORT_BYTES = 10 * 1024 * 1024


class DataPolicy(Model):
    archive_after_days: int | None = Field(default=None, ge=1, le=36500)
    schedule_interval_hours: int | None = Field(default=None, ge=1, le=8760)
    export_allowed: bool = True
    include_evidence: bool = True


class ApplyRetention(Model):
    as_of: datetime
    fingerprint: str = Field(pattern="^[a-f0-9]{64}$")


def event(
    db: sqlite3.Connection, workspace: UUID, action: str, metadata: dict[str, object]
) -> None:
    db.execute(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?)",
        (str(uuid4()), str(workspace), datetime.now(UTC).isoformat(), action, json.dumps(metadata)),
    )


class DataPolicies:
    def __init__(self, store: Store) -> None:
        self.store = store

    def _policy(self, db: sqlite3.Connection, workspace: UUID) -> DataPolicy:
        if db.execute("SELECT 1 FROM workspaces WHERE id=?", (str(workspace),)).fetchone() is None:
            raise ValueError("Workspace not found")
        row = db.execute(
            "SELECT payload FROM workspace_policies WHERE workspace_id=?", (str(workspace),)
        ).fetchone()
        return DataPolicy.model_validate_json(row[0]) if row else DataPolicy()

    def get(self, workspace: UUID) -> DataPolicy:
        with self.store.connection() as db:
            return self._policy(db, workspace)

    def scheduled(self, now: datetime | None = None) -> int:
        """Archive one bounded batch per due workspace, only after explicit policy opt-in."""
        now = now or datetime.now(UTC)
        archived = 0
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT workspace_id,payload FROM workspace_policies").fetchall()
            for identity, raw in rows:
                policy = DataPolicy.model_validate_json(raw)
                if policy.schedule_interval_hours is None or policy.archive_after_days is None:
                    continue
                last = db.execute(
                    "SELECT payload FROM workflow_records WHERE workspace_id=? "
                    "AND kind='retention_clock' AND identity='scheduled'",
                    (identity,),
                ).fetchone()
                if last and now - datetime.fromisoformat(
                    json.loads(last[0])["last_run"]
                ) < timedelta(hours=policy.schedule_interval_hours):
                    continue
                workspace = UUID(identity)
                preview = self._preview(db, workspace, now)
                identities = preview["run_ids"]
                assert isinstance(identities, list)
                db.executemany(
                    "INSERT INTO archived_runs VALUES (?,?)",
                    [(i, now.isoformat()) for i in identities],
                )
                db.execute(
                    "INSERT INTO workflow_records(workspace_id,kind,identity,payload) "
                    "VALUES (?,'retention_clock','scheduled',?) "
                    "ON CONFLICT(workspace_id,kind,identity) "
                    "DO UPDATE SET payload=excluded.payload",
                    (identity, json.dumps({"last_run": now.isoformat()})),
                )
                event(
                    db,
                    workspace,
                    "retention.scheduled",
                    {
                        "actor": "retention-scheduler",
                        "run_ids": identities,
                        "more": preview["more"],
                    },
                )
                archived += len(identities)
        return archived

    def save(self, workspace: UUID, policy: DataPolicy, actor: str) -> DataPolicy:
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._policy(db, workspace)
            db.execute(
                "INSERT INTO workspace_policies VALUES (?, ?) "
                "ON CONFLICT(workspace_id) DO UPDATE SET payload=excluded.payload",
                (str(workspace), policy.model_dump_json()),
            )
            event(db, workspace, "data_policy.updated", {"actor": actor, **policy.model_dump()})
        return policy

    def _preview(
        self, db: sqlite3.Connection, workspace: UUID, as_of: datetime
    ) -> dict[str, object]:
        policy = self._policy(db, workspace)
        candidates: list[str] = []
        more = False
        if policy.archive_after_days is not None:
            cutoff = as_of - timedelta(days=policy.archive_after_days)
            rows = db.execute(
                "SELECT r.id, r.payload FROM runs r WHERE r.workspace_id=? "
                "AND NOT EXISTS (SELECT 1 FROM archived_runs a WHERE a.run_id=r.id) "
                "AND NOT EXISTS (SELECT 1 FROM baselines b WHERE b.run_id=r.id) ORDER BY r.rowid",
                (str(workspace),),
            )
            for identity, payload in rows:
                if Run.model_validate_json(payload).created_at < cutoff:
                    if len(candidates) == 500:
                        more = True
                        break
                    candidates.append(identity)
        result: dict[str, object] = {
            "workspace_id": str(workspace),
            "as_of": as_of.isoformat(),
            "policy": policy.model_dump(),
            "run_ids": candidates,
            "count": len(candidates),
            "more": more,
            "operation": "archive",
            "baselines_protected": True,
        }
        return {**result, "fingerprint": digest(result)}

    def preview(self, workspace: UUID) -> dict[str, object]:
        with self.store.connection() as db:
            db.execute("BEGIN")
            return self._preview(db, workspace, datetime.now(UTC))

    def apply(self, workspace: UUID, request: ApplyRetention, actor: str) -> dict[str, object]:
        now = datetime.now(UTC)
        if request.as_of.tzinfo is None or not timedelta(0) <= now - request.as_of <= timedelta(
            minutes=10
        ):
            raise ValueError("Retention preview expired; preview again")
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            preview = self._preview(db, workspace, request.as_of)
            if preview["fingerprint"] != request.fingerprint:
                raise ValueError("Retention selection changed; preview again")
            identities = preview["run_ids"]
            assert isinstance(identities, list)
            db.executemany(
                "INSERT INTO archived_runs VALUES (?, ?)",
                [(identity, now.isoformat()) for identity in identities],
            )
            event(
                db,
                workspace,
                "retention.applied",
                {"actor": actor, "run_ids": identities, "fingerprint": request.fingerprint},
            )
        return {"archived": len(identities), "more": preview["more"]}

    def export(self, workspace: UUID, actor: str) -> dict[str, object]:
        # One transaction keeps the export internally consistent and the audit atomic.
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            policy = self._policy(db, workspace)
            if not policy.export_allowed:
                raise PermissionError("Workspace exports are disabled")
            remaining = MAX_EXPORT_BYTES
            entries: dict[str, object] = {}
            queries = {
                "workspace": "SELECT payload FROM workspaces WHERE id=?",
                "scope": "SELECT payload FROM scopes WHERE workspace_id=?",
                "runs": "SELECT payload FROM runs WHERE workspace_id=? ORDER BY rowid",
                "decisions": "SELECT payload FROM decisions WHERE workspace_id=? ORDER BY rowid",
                "workflow": "SELECT payload,identity,kind FROM workflow_records "
                "WHERE workspace_id=? "
                "AND kind IN ('triage','coverage','item','sprint','burndown') ORDER BY rowid",
                "baselines": "SELECT name, run_id FROM baselines "
                "WHERE workspace_id=? ORDER BY name",
                "archives": "SELECT a.run_id, a.archived_at FROM archived_runs a "
                "JOIN runs r ON r.id=a.run_id WHERE r.workspace_id=?",
            }
            if policy.include_evidence:
                queries["evidence"] = (
                    "SELECT payload, sha256, id, decision_id FROM evidence WHERE workspace_id=?"
                )
                queries["artifacts"] = (
                    "SELECT payload, sha256, id, kind, created_at FROM artifacts "
                    "WHERE workspace_id=?"
                )
            for kind, query in queries.items():
                values: list[object] = []
                for row in db.execute(query, (str(workspace),)):
                    if kind in {"baselines", "archives"}:
                        names = (
                            ("name", "run_id") if kind == "baselines" else ("run_id", "archived_at")
                        )
                        value = dict(zip(names, row, strict=True))
                        remaining -= len(json.dumps(value).encode()) + 512
                        if remaining < 0:
                            raise ValueError("Export exceeds 10 MiB")
                        values.append(value)
                        continue
                    raw = row[0].encode()
                    remaining -= len(raw) + 512
                    if remaining < 0:
                        raise ValueError("Export exceeds 10 MiB; export individual records instead")
                    value = json.loads(raw)
                    if kind == "workflow":
                        value = {"data": value, "id": row[1], "kind": row[2]}
                    elif len(row) > 1:
                        if hashlib.sha256(raw).hexdigest() != row[1]:
                            raise ValueError("Export integrity check failed")
                        value = {"data": value, "sha256": row[1]}
                    if kind == "evidence":
                        value.update(id=row[2], decision_id=row[3])
                    if kind == "artifacts":
                        value.update(id=row[2], kind=row[3], created_at=row[4])
                    values.append(value)
                entries[kind] = values
            body = {
                "format": "aegis-workspace-export-v1",
                "workspace_id": str(workspace),
                "created_at": datetime.now(UTC).isoformat(),
                "policy": policy.model_dump(),
                "records": entries,
            }
            checksum = digest(body)
            event(db, workspace, "workspace.exported", {"actor": actor, "sha256": checksum})
            return {"manifest_sha256": checksum, "bundle": body}
