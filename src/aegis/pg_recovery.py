"""Native PostgreSQL snapshot and restore exercise in a newly created database."""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from aegis.backup import checksum
from aegis.store import SCHEMA_VERSION


def inventory(db: Any, schema: str) -> str:
    from psycopg import sql

    tables = db.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename", (schema,)
    ).fetchall()
    digest = hashlib.sha256()
    remaining = 50 * 1024 * 1024
    for (table,) in tables:
        rows = db.execute(
            sql.SQL("SELECT * FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(table))
        ).fetchmany(50001)
        if len(rows) > 50000:
            raise ValueError("Recovery inventory exceeds record limit")
        encoded = sorted(json.dumps(row, default=str, separators=(",", ":")) for row in rows)
        raw = json.dumps([table, encoded], separators=(",", ":")).encode()
        remaining -= len(raw)
        if remaining < 0:
            raise ValueError("Recovery inventory exceeds 50 MiB")
        digest.update(raw)
    version = db.execute(
        sql.SQL("SELECT version FROM {}.aegis_schema_version WHERE singleton=1").format(
            sql.Identifier(schema)
        )
    ).fetchone()
    if version != (SCHEMA_VERSION,):
        raise ValueError("Recovery requires the current AEGIS schema")
    for table, content, expected in (
        ("evidence", "payload", "sha256"),
        ("artifacts", "payload", "sha256"),
        ("job_queue", "input", "input_sha256"),
    ):
        for raw, wanted in db.execute(
            sql.SQL("SELECT {},{} FROM {}.{}").format(
                sql.Identifier(content),
                sql.Identifier(expected),
                sql.Identifier(schema),
                sql.Identifier(table),
            )
        ):
            if hashlib.sha256(raw.encode()).hexdigest() != wanted:
                raise ValueError("Recovery content integrity failed")
    for (raw,) in db.execute(
        sql.SQL("SELECT payload FROM {}.workflow_records WHERE kind='deletion_tombstone'").format(
            sql.Identifier(schema)
        )
    ):
        tombstone = json.loads(raw)
        actual = hashlib.sha256(
            json.dumps(tombstone["manifest"], sort_keys=True).encode()
        ).hexdigest()
        if actual != tombstone["sha256"]:
            raise ValueError("Recovery tombstone integrity failed")
    for (sequence,) in db.execute(
        "SELECT sequencename FROM pg_sequences WHERE schemaname=%s ORDER BY sequencename",
        (schema,),
    ).fetchall():
        value = db.execute(
            sql.SQL("SELECT last_value,is_called FROM {}.{}").format(
                sql.Identifier(schema), sql.Identifier(sequence)
            )
        ).fetchone()
        digest.update(json.dumps([sequence, value], default=str).encode())
    return digest.hexdigest()


def verify_recovery(url: str, directory: Path, bin_dir: Path) -> dict[str, Any]:
    """Never restore into an existing database. Never expose connection credentials."""
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from aegis.postgres import LOCK_ID

    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (uuid4().hex + ".pgdump")
    # Exclusive file creation; the archive is retained even when verification fails.
    destination.touch(exist_ok=False, mode=0o600)
    parameters = {
        key: str(value) for key, value in conninfo_to_dict(url).items() if value is not None
    }
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    mapping = {
        "host": "PGHOST",
        "hostaddr": "PGHOSTADDR",
        "port": "PGPORT",
        "user": "PGUSER",
        "password": "PGPASSWORD",
        "dbname": "PGDATABASE",
        "sslmode": "PGSSLMODE",
        "sslcert": "PGSSLCERT",
        "sslkey": "PGSSLKEY",
        "sslrootcert": "PGSSLROOTCERT",
        "options": "PGOPTIONS",
    }
    if set(parameters) - set(mapping):
        raise ValueError("Unsupported recovery connection option")
    env.update({mapping[key]: value for key, value in parameters.items()})
    env["PGCONNECT_TIMEOUT"] = "5"
    suffix = ".exe" if os.name == "nt" else ""
    dump, restore = [bin_dir.resolve() / (name + suffix) for name in ("pg_dump", "pg_restore")]
    if not dump.is_file() or not restore.is_file():
        raise ValueError("Native PostgreSQL tools are missing")
    restored_name = "aegis_recovery_" + uuid4().hex
    created = False
    try:
        with psycopg.connect(url, autocommit=True, connect_timeout=5) as admin:
            try:
                with psycopg.connect(url, connect_timeout=5) as source:
                    source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    source.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
                    schema_row = source.execute("SELECT current_schema()").fetchone()
                    assert schema_row is not None
                    schema = schema_row[0]
                    if not schema or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
                        raise ValueError("Unsupported source schema name")
                    snapshot_row = source.execute("SELECT pg_export_snapshot()").fetchone()
                    assert snapshot_row is not None
                    snapshot = snapshot_row[0]
                    expected = inventory(source, schema)
                    subprocess.run(
                        [
                            str(dump),
                            "--format=custom",
                            "--no-owner",
                            "--no-acl",
                            "--no-password",
                            "--schema=" + schema,
                            "--snapshot=" + snapshot,
                            "--file=" + str(destination.resolve()),
                        ],
                        env=env,
                        check=True,
                        capture_output=True,
                        timeout=120,
                    )
                admin.execute(
                    sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                        sql.Identifier(restored_name)
                    )
                )
                created = True
                restored_env = {**env, "PGDATABASE": restored_name}
                restored_env.pop("PGOPTIONS", None)
                subprocess.run(
                    [
                        str(restore),
                        "--no-owner",
                        "--no-acl",
                        "--no-password",
                        "--exit-on-error",
                        "--single-transaction",
                        "--dbname=" + restored_name,
                        str(destination.resolve()),
                    ],
                    env=restored_env,
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
                restored_parameters = {**parameters, "dbname": restored_name}
                restored_parameters.pop("options", None)
                with psycopg.connect(
                    make_conninfo(**restored_parameters), connect_timeout=5
                ) as restored:
                    if inventory(restored, schema) != expected:
                        raise ValueError("Restored inventory does not match the snapshot")
                return {
                    "sha256": checksum(destination),
                    "inventory_sha256": expected,
                    "path": str(destination.resolve()),
                    "backend": "postgresql",
                }
            finally:
                if created:
                    # Only this call's newly created, random database may be removed.
                    admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(restored_name)))
    except (psycopg.Error, subprocess.SubprocessError, OSError):
        raise ValueError(
            "Native PostgreSQL recovery verification failed; archive retained"
        ) from None
