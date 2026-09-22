"""Optional PostgreSQL transport for the workbench's internal parameterized SQL."""

import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg

LOCK_ID = 1095063891


class Connection:
    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self.connection = connection

    def execute(self, statement: str, parameters: Sequence[object] = ()) -> Any:
        # Only application-owned SQL reaches this interface; data always uses parameters.
        if statement in {"BEGIN", "BEGIN IMMEDIATE"}:
            statement = "SELECT 1"  # The enclosing context already opened a transaction.
        if "INSERT OR IGNORE INTO" in statement:
            statement = statement.replace("INSERT OR IGNORE INTO", "INSERT INTO")
            statement += " ON CONFLICT DO NOTHING"
        statement = statement.replace("?", "%s")
        return self.connection.execute(statement, parameters)

    def executemany(self, statement: str, parameters: Sequence[Sequence[object]]) -> None:
        with self.connection.cursor() as cursor:
            cursor.executemany(statement.replace("?", "%s"), parameters)


@contextmanager
def connect(url: str) -> Iterator[Connection]:
    try:
        with psycopg.connect(url, connect_timeout=5) as raw:
            # Match SQLite's serialized workbench transactions. Also protects migrations,
            # retention previews/apply and exports from concurrent metadata changes.
            raw.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
            yield Connection(raw)
    except psycopg.Error:
        # Connection strings and database values must never enter API error responses.
        raise sqlite3.OperationalError("PostgreSQL operation failed") from None


def migrate(db: Connection, migrations: dict[int, str], version: int) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS aegis_schema_version "
        "(singleton INTEGER PRIMARY KEY CHECK (singleton=1), version INTEGER NOT NULL)"
    )
    row = db.execute("SELECT version FROM aegis_schema_version WHERE singleton=1").fetchone()
    current = row[0] if row else 0
    if current not in range(version + 1):
        raise ValueError("Unsupported PostgreSQL schema version")
    for next_version in range(current + 1, version + 1):
        for statement in migrations[next_version].split(";"):
            statement = statement.strip()
            if not statement:
                continue
            if re.match(r"CREATE TABLE \w+", statement):
                # Stable insertion order is explicit in PostgreSQL, unlike SQLite rowid.
                end = statement.rfind(")")
                statement = statement[:end] + ", rowid BIGSERIAL UNIQUE" + statement[end:]
            statement = statement.replace(
                "DEFAULT CURRENT_TIMESTAMP", "DEFAULT (CURRENT_TIMESTAMP::text)"
            )
            db.execute(statement)
        db.execute(
            "INSERT INTO aegis_schema_version VALUES (1, ?) "
            "ON CONFLICT(singleton) DO UPDATE SET version=excluded.version",
            (next_version,),
        )
