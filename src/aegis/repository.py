"""Workspace application persistence; bounded listings and recoverable retention."""

import hashlib
import json
from contextvars import ContextVar
from datetime import UTC, datetime
from uuid import UUID, uuid4

from aegis.assurance import Run
from aegis.policy import Scope, Workspace
from aegis.store import Store

current_actor: ContextVar[str] = ContextVar("aegis_actor", default="system")


def validate_page(limit: int, offset: int) -> None:
    if not 1 <= limit <= 200 or offset < 0:
        raise ValueError("Page limit must be 1-200 and offset must be nonnegative")


class Repository:
    def __init__(self, store: Store) -> None:
        self.store = store

    def workspaces(
        self, *, limit: int = 200, offset: int = 0, allowed_ids: tuple[UUID, ...] | None = None
    ) -> list[Workspace]:
        validate_page(limit, offset)
        where = ""
        params: list[str | int] = []
        if allowed_ids is not None:
            if not allowed_ids:
                return []
            where = "WHERE id IN (" + ",".join("?" for _ in allowed_ids) + ") "
            params.extend(str(identity) for identity in allowed_ids)
        params.extend([limit, offset])
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT payload FROM workspaces " + where + "ORDER BY rowid DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [Workspace.model_validate_json(row[0]) for row in rows]

    def audit(self, workspace_id: UUID | None, action: str, metadata: dict[str, object]) -> str:
        identity = str(uuid4())
        with self.store.connection() as db:
            db.execute(
                "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?)",
                (
                    identity,
                    str(workspace_id) if workspace_id else None,
                    datetime.now(UTC).isoformat(),
                    action,
                    json.dumps({"actor": current_actor.get(), **metadata}),
                ),
            )
        return identity

    def events(
        self, workspace_id: UUID, *, limit: int = 200, offset: int = 0
    ) -> list[dict[str, object]]:
        validate_page(limit, offset)
        self.store.workspace(workspace_id)
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT id, created_at, action, payload FROM audit_events "
                "WHERE workspace_id = ? ORDER BY rowid DESC LIMIT ? OFFSET ?",
                (str(workspace_id), limit, offset),
            ).fetchall()
        return [
            {"id": r[0], "timestamp": r[1], "action": r[2], "metadata": json.loads(r[3])}
            for r in rows
        ]

    def scope(self, workspace_id: UUID) -> Scope | None:
        self.store.workspace(workspace_id)
        with self.store.connection() as db:
            row = db.execute(
                "SELECT payload FROM scopes WHERE workspace_id = ?", (str(workspace_id),)
            ).fetchone()
        return Scope.model_validate_json(row[0]) if row else None

    def save_scope(self, scope: Scope) -> None:
        self.store.workspace(scope.workspace_id)
        with self.store.connection() as db:
            db.execute(
                "INSERT INTO scopes VALUES (?, ?) ON CONFLICT(workspace_id) "
                "DO UPDATE SET payload=excluded.payload",
                (str(scope.workspace_id), scope.model_dump_json()),
            )
        self.audit(
            scope.workspace_id,
            "scope.updated",
            {
                "sha256": hashlib.sha256(scope.model_dump_json().encode()).hexdigest(),
            },
        )

    def runs(
        self, workspace_id: UUID, *, archived: bool = False, limit: int = 200, offset: int = 0
    ) -> list[Run]:
        validate_page(limit, offset)
        self.store.workspace(workspace_id)
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT r.payload FROM runs r LEFT JOIN archived_runs a ON a.run_id=r.id "
                "WHERE r.workspace_id=? AND (a.run_id IS NOT NULL)=? "
                "ORDER BY r.rowid DESC LIMIT ? OFFSET ?",
                (str(workspace_id), archived, limit, offset),
            ).fetchall()
        return [Run.model_validate_json(r[0]) for r in rows]

    def baselines(
        self, workspace_id: UUID, *, limit: int = 200, offset: int = 0
    ) -> list[dict[str, str]]:
        validate_page(limit, offset)
        self.store.workspace(workspace_id)
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT name, run_id FROM baselines WHERE workspace_id=? "
                "ORDER BY name LIMIT ? OFFSET ?",
                (str(workspace_id), limit, offset),
            ).fetchall()
        return [{"name": r[0], "run_id": r[1]} for r in rows]

    def archive(self, workspace_id: UUID, run_id: UUID, archived: bool) -> None:
        self.store.run(run_id, workspace_id)
        with self.store.connection() as db:
            if archived:
                db.execute(
                    "INSERT OR IGNORE INTO archived_runs VALUES (?, ?)",
                    (str(run_id), datetime.now(UTC).isoformat()),
                )
            else:
                db.execute("DELETE FROM archived_runs WHERE run_id=?", (str(run_id),))
        self.audit(
            workspace_id, "run.archived" if archived else "run.restored", {"run_id": str(run_id)}
        )

    def add_artifact(
        self, workspace_id: UUID, kind: str, data: dict[str, object]
    ) -> dict[str, object]:
        self.store.workspace(workspace_id)
        identity = str(uuid4())
        created_at = datetime.now(UTC).isoformat()
        payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
        checksum = hashlib.sha256(payload.encode()).hexdigest()
        with self.store.connection() as db:
            db.execute(
                "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?)",
                (
                    identity,
                    str(workspace_id),
                    kind,
                    created_at,
                    checksum,
                    payload,
                ),
            )
        self.audit(workspace_id, "artifact.created", {"artifact_id": identity, "kind": kind})
        return {
            "id": identity,
            "kind": kind,
            "created_at": created_at,
            "sha256": checksum,
            "data": data,
        }

    def artifacts(
        self, workspace_id: UUID, *, limit: int = 200, offset: int = 0
    ) -> list[dict[str, object]]:
        validate_page(limit, offset)
        self.store.workspace(workspace_id)
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT id, kind, created_at, sha256, payload FROM artifacts "
                "WHERE workspace_id=? ORDER BY rowid DESC LIMIT ? OFFSET ?",
                (str(workspace_id), limit, offset),
            ).fetchall()
        result = []
        for row in rows:
            if hashlib.sha256(row[4].encode()).hexdigest() != row[3]:
                raise ValueError("Artifact integrity check failed")
            result.append(
                {
                    "id": row[0],
                    "kind": row[1],
                    "created_at": row[2],
                    "sha256": row[3],
                    "data": json.loads(row[4]),
                }
            )
        return result
