"""Revocable, workspace-scoped local credentials; only token digests are stored."""

import hashlib
import json
import secrets
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from aegis.policy import Model
from aegis.store import Store


class NewKey(Model):
    label: str = Field(min_length=1, max_length=100)
    role: Literal["reader", "analyst", "admin"]
    workspace_ids: tuple[UUID, ...] = Field(default=(), max_length=200)

    @model_validator(mode="after")
    def scoped(self) -> "NewKey":
        if self.role != "admin" and not self.workspace_ids:
            raise ValueError("Reader and analyst keys require workspace scope")
        if self.role == "admin" and self.workspace_ids:
            raise ValueError("Administrator keys apply to all workspaces")
        if len(set(self.workspace_ids)) != len(self.workspace_ids):
            raise ValueError("Duplicate workspace")
        return self


class Principal(Model):
    id: str
    role: Literal["reader", "analyst", "admin"]
    workspace_ids: tuple[UUID, ...] = ()


class Access:
    def __init__(self, store: Store) -> None:
        self.store = store

    def create(self, key: NewKey) -> dict[str, object]:
        for workspace_id in key.workspace_ids:
            self.store.workspace(workspace_id)
        identity, token = str(uuid4()), secrets.token_urlsafe(32)
        created = datetime.now(UTC).isoformat()
        with self.store.connection() as db:
            db.execute(
                "INSERT INTO access_keys VALUES (?, ?, ?, ?, ?, ?, 0)",
                (
                    identity,
                    key.label,
                    key.role,
                    json.dumps([str(w) for w in key.workspace_ids]),
                    hashlib.sha256(token.encode()).hexdigest(),
                    created,
                ),
            )
        return {
            "id": identity,
            "label": key.label,
            "role": key.role,
            "token": token,
            "workspace_ids": [str(w) for w in key.workspace_ids],
            "created_at": created,
        }

    def lookup(self, token: str) -> Principal | None:
        with self.store.connection() as db:
            row = db.execute(
                "SELECT id, role, workspace_ids FROM access_keys WHERE token_hash=? AND revoked=0",
                (hashlib.sha256(token.encode()).hexdigest(),),
            ).fetchone()
        if row is None:
            return None
        return Principal(id=row[0], role=row[1], workspace_ids=json.loads(row[2]))

    def active(self, identity: str) -> bool:
        with self.store.connection() as db:
            return (
                db.execute(
                    "SELECT 1 FROM access_keys WHERE id=? AND revoked=0", (identity,)
                ).fetchone()
                is not None
            )

    def list(self, limit: int = 50, offset: int = 0) -> list[dict[str, object]]:
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT id, label, role, workspace_ids, created_at, revoked "
                "FROM access_keys ORDER BY rowid DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [
            {
                "id": r[0],
                "label": r[1],
                "role": r[2],
                "workspace_ids": json.loads(r[3]),
                "created_at": r[4],
                "revoked": bool(r[5]),
            }
            for r in rows
        ]

    def revoke(self, identity: UUID) -> None:
        with self.store.connection() as db:
            if (
                db.execute("UPDATE access_keys SET revoked=1 WHERE id=?", (str(identity),)).rowcount
                != 1
            ):
                raise ValueError("Access key not found")
