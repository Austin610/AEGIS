from pathlib import Path

import pytest

from aegis.backup import restore, snapshot
from aegis.policy import Workspace
from aegis.repository import Repository
from aegis.store import Store


def test_backup_restore_preserves_records_and_rejects_overwrite(tmp_path: Path) -> None:
    store = Store(tmp_path / "live.db")
    workspace = Workspace(name="Recovery test")
    store.add_workspace(workspace)
    repository = Repository(store)
    original = repository.add_artifact(workspace.id, "note", {"note": "Important"})
    backup = tmp_path / "backup.db"
    result = snapshot(store.path, backup)
    destination = tmp_path / "recovered.db"
    restore(backup, destination, str(result["sha256"]))
    recovered = Repository(Store(destination))
    assert recovered.artifacts(workspace.id) == [original]
    with pytest.raises(FileExistsError):
        snapshot(store.path, backup)
    with pytest.raises(FileExistsError):
        restore(backup, store.path, str(result["sha256"]))
    with pytest.raises(ValueError, match="checksum"):
        restore(backup, tmp_path / "bad.db", "0" * 64)
    assert not (tmp_path / "bad.db").exists()


def test_backup_rejects_tampered_evidence_and_cleans_output(tmp_path: Path) -> None:
    store = Store(tmp_path / "live.db")
    workspace = Workspace(name="Tamper check")
    store.add_workspace(workspace)
    Repository(store).add_artifact(workspace.id, "note", {"note": "Original"})
    with store.connection() as db:
        db.execute("UPDATE artifacts SET payload='{}'")
    destination = tmp_path / "bad.db"
    with pytest.raises(ValueError, match="Evidence integrity"):
        snapshot(store.path, destination)
    assert not destination.exists()
