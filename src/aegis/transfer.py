"""One-way, atomic SQLite-to-PostgreSQL transfer into an empty AEGIS database."""

import sqlite3
from contextlib import closing
from pathlib import Path

from aegis.backup import validate
from aegis.postgres import connect, migrate
from aegis.store import MIGRATIONS, SCHEMA_VERSION

TABLES = (
    "workspaces",
    "decisions",
    "evidence",
    "runs",
    "baselines",
    "scopes",
    "audit_events",
    "artifacts",
    "archived_runs",
    "jobs",
    "workspace_policies",
    "access_keys",
    "workflow_records",
    "job_queue",
)


def transfer(source: Path, destination_url: str) -> dict[str, int]:
    if not destination_url.startswith(("postgresql://", "postgres://")):
        raise ValueError("A PostgreSQL destination is required")
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as incoming:
        incoming.execute("BEGIN")
        validate(incoming)
        if incoming.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise ValueError("Upgrade the SQLite source with the current application first")
        if incoming.execute(
            "SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1"
        ).fetchone():
            raise ValueError("Drain active jobs and stop workers before transferring")
        with connect(destination_url) as outgoing:
            migrate(
                outgoing,
                MIGRATIONS,
                SCHEMA_VERSION,
            )
            for table in TABLES:
                if outgoing.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                    raise ValueError("Destination must be empty; no records were copied")
            counts = {}
            for table in TABLES:
                rows = incoming.execute(f"SELECT * FROM {table} ORDER BY rowid")
                assert rows.description is not None
                columns = [column[0] for column in rows.description]
                # Identifiers come solely from the checked AEGIS schema's fixed tables.
                if any(not name.replace("_", "").isalnum() for name in columns):
                    raise ValueError("Unexpected source column")
                query = (
                    f"INSERT INTO {table} ({','.join(columns)}) "
                    f"VALUES ({','.join('?' for _ in columns)})"
                )
                count = 0
                while batch := rows.fetchmany(200):
                    outgoing.executemany(query, batch)
                    count += len(batch)
                counts[table] = count
            mode = incoming.execute("SELECT mode FROM job_runtime WHERE id=1").fetchone()[0]
            outgoing.execute("UPDATE job_runtime SET mode=? WHERE id=1", (mode,))
            return counts
