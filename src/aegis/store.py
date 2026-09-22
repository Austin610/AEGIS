"""Transactional local SQLite storage with versioned schema and immutable records."""

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

from aegis.assurance import Run
from aegis.policy import Decision, Workspace

SCHEMA = """
CREATE TABLE workspaces (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE decisions (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL
 REFERENCES workspaces(id), payload TEXT NOT NULL);
CREATE TABLE evidence (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL
 REFERENCES workspaces(id), decision_id TEXT NOT NULL REFERENCES decisions(id),
 sha256 TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE runs (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL
 REFERENCES workspaces(id), evidence_id TEXT NOT NULL REFERENCES evidence(id),
 payload TEXT NOT NULL);
CREATE TABLE baselines (workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 name TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(id), PRIMARY KEY(workspace_id, name));
"""

MIGRATION_2 = """
CREATE INDEX runs_by_workspace ON runs(workspace_id);
CREATE INDEX evidence_by_workspace ON evidence(workspace_id);
CREATE INDEX decisions_by_workspace ON decisions(workspace_id);
CREATE TABLE schema_migrations (
 version INTEGER PRIMARY KEY, description TEXT NOT NULL,
 applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
INSERT INTO schema_migrations(version, description) VALUES (1, 'Initial offline schema');
INSERT INTO schema_migrations(version, description) VALUES (2, 'Indexes and migration history');
"""

MIGRATION_3 = """
CREATE TABLE scopes (workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id),
 payload TEXT NOT NULL);
CREATE TABLE audit_events (id TEXT PRIMARY KEY, workspace_id TEXT REFERENCES workspaces(id),
 created_at TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE artifacts (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 kind TEXT NOT NULL, created_at TEXT NOT NULL, sha256 TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE archived_runs (run_id TEXT PRIMARY KEY REFERENCES runs(id), archived_at TEXT NOT NULL);
CREATE TABLE jobs (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 status TEXT NOT NULL, payload TEXT NOT NULL);
INSERT INTO schema_migrations(version, description) VALUES (3, 'Scopes, audit, artifacts and jobs');
"""


MIGRATION_4 = """
CREATE TABLE workspace_policies (workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id),
 payload TEXT NOT NULL);
CREATE TABLE access_keys (id TEXT PRIMARY KEY, label TEXT NOT NULL, role TEXT NOT NULL,
 workspace_ids TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL,
 revoked INTEGER NOT NULL DEFAULT 0);
CREATE INDEX artifacts_by_workspace ON artifacts(workspace_id);
CREATE INDEX jobs_by_workspace ON jobs(workspace_id);
CREATE INDEX audit_by_workspace ON audit_events(workspace_id);
INSERT INTO schema_migrations(version, description)
 VALUES (4, 'Data policies and scoped access keys');
"""
MIGRATION_5 = """
CREATE TABLE workflow_records (workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 kind TEXT NOT NULL, identity TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(workspace_id, kind, identity));
INSERT INTO schema_migrations(version, description) VALUES (5, 'Evidence-linked workflow records');
"""
MIGRATION_6 = """
CREATE TABLE job_runtime (id INTEGER PRIMARY KEY CHECK (id=1), mode TEXT NOT NULL);
INSERT INTO job_runtime(id,mode) VALUES (1,'legacy');
CREATE TABLE job_queue (job_id TEXT PRIMARY KEY REFERENCES jobs(id), input TEXT NOT NULL,
 input_sha256 TEXT NOT NULL, adapter_version TEXT NOT NULL, actor TEXT NOT NULL,
 owner TEXT, lease_until TEXT, attempts INTEGER NOT NULL DEFAULT 0);
INSERT INTO schema_migrations(version, description)
 VALUES (6, 'Durable offline queue and worker fencing');
"""
SCHEMA_VERSION = 6
MIGRATIONS = {
    1: SCHEMA,
    2: MIGRATION_2,
    3: MIGRATION_3,
    4: MIGRATION_4,
    5: MIGRATION_5,
    6: MIGRATION_6,
}


def migrate(db: sqlite3.Connection) -> None:
    """Upgrade in a single serialized transaction; future schemas are rejected."""
    db.execute("BEGIN IMMEDIATE")
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version not in range(SCHEMA_VERSION + 1):
        raise ValueError("Unsupported database schema version")
    migrations = MIGRATIONS
    for next_version in range(version + 1, SCHEMA_VERSION + 1):
        for statement in migrations[next_version].split(";"):
            if statement.strip():
                db.execute(statement)
        # next_version is an internal constant, never input.
        db.execute(f"PRAGMA user_version = {next_version}")


class Store:
    def __init__(self, path: Path | str) -> None:
        self.database_url = (
            str(path) if str(path).startswith(("postgresql://", "postgres://")) else None
        )
        if self.database_url:
            from aegis.postgres import migrate as migrate_postgres

            with self.connection() as db:
                migrate_postgres(
                    db,
                    MIGRATIONS,
                    SCHEMA_VERSION,
                )
            return
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connection() as db:
            migrate(db)

    @contextmanager
    def connection(self) -> Iterator[Any]:
        if self.database_url:
            from aegis.postgres import connect

            with connect(self.database_url) as connection:
                yield connection
            return
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_workspace(self, workspace: Workspace) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT INTO workspaces VALUES (?, ?)",
                (str(workspace.id), workspace.model_dump_json()),
            )

    def workspace(self, identity: UUID) -> Workspace:
        with self.connection() as db:
            row = db.execute(
                "SELECT payload FROM workspaces WHERE id = ?", (str(identity),)
            ).fetchone()
        if row is None:
            raise ValueError("Workspace not found")
        return Workspace.model_validate_json(row[0])

    def record_decision(self, decision: Decision) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT INTO decisions VALUES (?, ?, ?)",
                (str(decision.id), str(decision.workspace_id), decision.model_dump_json()),
            )

    def save_run(self, run: Run, evidence: dict[str, object]) -> None:
        payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self.connection() as db:
            row = db.execute(
                "SELECT payload FROM decisions WHERE id = ?", (str(run.decision_id),)
            ).fetchone()
            if row is None:
                raise ValueError("Missing scope decision")
            decision = Decision.model_validate_json(row[0])
            if not decision.allowed or decision.workspace_id != run.workspace_id:
                raise ValueError("Run requires allowed decision in the same workspace")
            if decision.target != run.target or decision.capability != "fixture.evaluate":
                raise ValueError("Scope decision does not match run target or capability")
            db.execute(
                "INSERT INTO evidence VALUES (?, ?, ?, ?, ?)",
                (
                    str(run.evidence_id),
                    str(run.workspace_id),
                    str(run.decision_id),
                    digest,
                    payload,
                ),
            )
            db.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?)",
                (
                    str(run.id),
                    str(run.workspace_id),
                    str(run.evidence_id),
                    run.model_dump_json(),
                ),
            )

    def run(self, identity: UUID, workspace_id: UUID) -> Run:
        with self.connection() as db:
            row = db.execute(
                "SELECT payload FROM runs WHERE id = ? AND workspace_id = ?",
                (str(identity), str(workspace_id)),
            ).fetchone()
        if row is None:
            raise ValueError("Run not found in workspace")
        return Run.model_validate_json(row[0])

    def evidence(self, identity: UUID, workspace_id: UUID) -> dict[str, object]:
        with self.connection() as db:
            row = db.execute(
                "SELECT sha256, payload FROM evidence WHERE id = ? AND workspace_id = ?",
                (str(identity), str(workspace_id)),
            ).fetchone()
        if row is None or hashlib.sha256(row[1].encode()).hexdigest() != row[0]:
            raise ValueError("Evidence missing or integrity check failed")
        value: dict[str, object] = json.loads(row[1])
        return value

    def baseline(self, workspace_id: UUID, name: str, run_id: UUID) -> None:
        self.run(run_id, workspace_id)
        with self.connection() as db:
            db.execute(
                "INSERT INTO baselines VALUES (?, ?, ?)", (str(workspace_id), name, str(run_id))
            )

    def get_baseline(self, workspace_id: UUID, name: str) -> Run:
        with self.connection() as db:
            row = db.execute(
                "SELECT run_id FROM baselines WHERE workspace_id = ? AND name = ?",
                (str(workspace_id), name),
            ).fetchone()
        if row is None:
            raise ValueError("Baseline not found")
        return self.run(UUID(row[0]), workspace_id)


class TransactionStore(Store):
    """Repository operations bound to an existing transaction, without nested connections."""

    def __init__(self, connection: Any) -> None:
        self.bound_connection = connection

    @contextmanager
    def connection(self) -> Iterator[Any]:
        yield self.bound_connection
