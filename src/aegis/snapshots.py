"""Bounded immutable list snapshots, scoped to the requesting principal and workspace."""

import hashlib
import json
import secrets
import threading
import time
from typing import Any
from uuid import UUID

from aegis.repository import Repository, validate_page


class Snapshots:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository
        self.cache: dict[str, tuple[float, tuple[str, str, str, bool], list[Any], int]] = {}
        self.lock = threading.Lock()

    def page(
        self,
        principal: str,
        workspace: UUID,
        resource: str,
        cursor: str | None,
        limit: int,
        offset: int,
        archived: bool = False,
    ) -> dict[str, Any]:
        validate_page(limit, offset)
        binding = (principal, str(workspace), resource, archived)
        with self.lock:
            now = time.monotonic()
            self.cache = {k: v for k, v in self.cache.items() if v[0] > now}
            if cursor:
                saved = self.cache.get(cursor)
                if saved is None or saved[1] != binding:
                    raise ValueError("Snapshot expired or belongs to another context")
                rows = saved[2]
            else:
                rows = self.capture(workspace, resource, archived)
                size = len(json.dumps(rows).encode())
                if size > 10 * 1024 * 1024:
                    raise ValueError("Snapshot exceeds 10 MiB")
                while self.cache and (
                    len(self.cache) >= 32
                    or sum(v[3] for v in self.cache.values()) + size > 50 * 1024 * 1024
                ):
                    self.cache.pop(next(iter(self.cache)))
                cursor = secrets.token_urlsafe(32)
                self.cache[cursor] = (now + 300, binding, rows, size)
            return {
                "cursor": cursor,
                "items": rows[offset : offset + limit],
                "total": len(rows),
                "offset": offset,
                "expires_in_seconds": max(0, int(self.cache[cursor][0] - now)),
            }

    def capture(self, workspace: UUID, resource: str, archived: bool) -> list[Any]:
        queries = {
            "runs": "SELECT r.payload FROM runs r LEFT JOIN archived_runs a ON a.run_id=r.id "
            "WHERE r.workspace_id=? AND (a.run_id IS NOT NULL)=? ORDER BY r.rowid DESC",
            "jobs": "SELECT payload FROM jobs WHERE workspace_id=? ORDER BY rowid DESC",
            "artifacts": "SELECT payload,id,kind,created_at,sha256 FROM artifacts "
            "WHERE workspace_id=? ORDER BY rowid DESC",
            "baselines": "SELECT name,run_id FROM baselines WHERE workspace_id=? ORDER BY name",
            "audit": "SELECT payload,id,created_at,action FROM audit_events "
            "WHERE workspace_id=? ORDER BY rowid DESC",
        }
        if resource not in queries:
            raise ValueError("Unknown snapshot resource")
        self.repository.store.workspace(workspace)
        remaining = 10 * 1024 * 1024
        result: list[Any] = []
        with self.repository.store.connection() as db:
            db.execute("BEGIN")
            params: tuple[object, ...] = (
                (str(workspace), archived) if resource == "runs" else (str(workspace),)
            )
            for row in db.execute(queries[resource], params):
                remaining -= sum(len(str(v).encode()) for v in row) + 512
                if remaining < 0 or len(result) >= 10000:
                    raise ValueError("Snapshot exceeds capacity")
                if resource == "baselines":
                    value = {"name": row[0], "run_id": row[1]}
                else:
                    value = json.loads(row[0])
                    if resource == "artifacts":
                        if hashlib.sha256(row[0].encode()).hexdigest() != row[4]:
                            raise ValueError("Artifact integrity check failed")
                        value = {
                            "data": value,
                            "id": row[1],
                            "kind": row[2],
                            "created_at": row[3],
                            "sha256": row[4],
                        }
                    elif resource == "audit":
                        value = {
                            "metadata": value,
                            "id": row[1],
                            "timestamp": row[2],
                            "action": row[3],
                        }
                result.append(value)
        return result
