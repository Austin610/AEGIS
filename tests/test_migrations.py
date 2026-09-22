import sqlite3
from pathlib import Path

import pytest

from aegis.policy import Workspace
from aegis.store import SCHEMA, Store


def test_upgrade_v1_preserves_workspace(tmp_path: Path) -> None:
    path = tmp_path / "aegis.db"
    workspace = Workspace(name="legacy")
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute("PRAGMA user_version = 1")
        db.execute(
            "INSERT INTO workspaces VALUES (?, ?)", (str(workspace.id), workspace.model_dump_json())
        )
    store = Store(path)
    assert store.workspace(workspace.id) == workspace
    with store.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6
        assert db.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall() == [
            (1,),
            (2,),
            (3,),
            (4,),
            (5,),
            (6,),
        ]
    # Opening again must be idempotent.
    assert Store(path).workspace(workspace.id) == workspace


def test_migration_failure_rolls_back(tmp_path: Path) -> None:
    path = tmp_path / "aegis.db"
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute("PRAGMA user_version = 1")
        db.execute("CREATE TABLE schema_migrations (conflicting INTEGER)")
    with pytest.raises(sqlite3.OperationalError):
        Store(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert (
            db.execute("SELECT name FROM sqlite_master WHERE name='runs_by_workspace'").fetchone()
            is None
        )
