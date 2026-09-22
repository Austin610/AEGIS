"""Consistent SQLite snapshots and verified restores to new files only."""

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from aegis.store import SCHEMA_VERSION


def checksum(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate(db: sqlite3.Connection) -> None:
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version not in range(1, SCHEMA_VERSION + 1):
        raise ValueError("Unsupported backup schema")
    if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise ValueError("Database integrity check failed")
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("Database contains invalid references")
    tables = ["evidence"] + (["artifacts"] if version >= 3 else [])
    for table in tables:
        for payload, expected in db.execute(f"SELECT payload, sha256 FROM {table}"):
            if hashlib.sha256(payload.encode()).hexdigest() != expected:
                raise ValueError("Evidence integrity check failed")
    if version >= 6:
        for raw, expected in db.execute("SELECT input,input_sha256 FROM job_queue"):
            if hashlib.sha256(raw.encode()).hexdigest() != expected:
                raise ValueError("Queued input integrity check failed")
    if version >= 5:
        for (raw,) in db.execute(
            "SELECT payload FROM workflow_records WHERE kind='deletion_tombstone'"
        ):
            tombstone = json.loads(raw)
            actual = hashlib.sha256(
                json.dumps(tombstone["manifest"], sort_keys=True).encode()
            ).hexdigest()
            if actual != tombstone["sha256"]:
                raise ValueError("Deletion tombstone integrity check failed")


def snapshot(source: Path, destination: Path) -> dict[str, object]:
    source, destination = source.resolve(), destination.absolute()
    if not source.is_file():
        raise ValueError("Source database does not exist")
    # Exclusive creation prevents overwriting either live data or an existing backup.
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as incoming:
            with closing(sqlite3.connect(destination)) as outgoing:
                incoming.backup(outgoing)
                validate(outgoing)
        return {
            "path": str(destination),
            "sha256": checksum(destination),
            "bytes": destination.stat().st_size,
        }
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def restore(source: Path, destination: Path, expected_sha256: str) -> dict[str, object]:
    if checksum(source) != expected_sha256:
        raise ValueError("Backup checksum does not match")
    result = snapshot(source, destination)
    # Check the copied snapshot too, rejecting a source that changed during restore.
    if result["sha256"] != expected_sha256:
        destination.unlink(missing_ok=True)
        raise ValueError("Backup changed during restore")
    return result
