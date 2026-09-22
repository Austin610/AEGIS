import os
import subprocess
from pathlib import Path

import pytest
from test_api import create_workspace
from test_deletion import archived_run, preview


@pytest.mark.parametrize("failure", ["restore", "inventory"])
def test_native_recovery_failure_preserves_source_and_cleans_created_database(
    client, monkeypatch, failure
):
    store = client.app.state.repository.store
    bin_dir = os.environ.get("AEGIS_TEST_POSTGRES_BIN")
    if not store.database_url or not bin_dir:
        pytest.skip("Native PostgreSQL integration configuration required")
    import psycopg

    import aegis.pg_recovery as recovery

    workspace = create_workspace(client)
    run = archived_run(client, workspace)
    manager = client.app.state.deletion
    manager.grace_days = 30
    manager.pg_bin = Path(bin_dir)
    manager.recovery_dir = client.app.state.test_directory / "recovery"
    with psycopg.connect(store.database_url) as db:
        before = db.execute("SELECT datname FROM pg_database ORDER BY datname").fetchall()
    if failure == "restore":
        original = recovery.subprocess.run

        def fail_restore(args, **kwargs):
            if Path(args[0]).stem == "pg_restore":
                raise subprocess.CalledProcessError(1, args, stderr="private database details")
            return original(args, **kwargs)

        monkeypatch.setattr(recovery.subprocess, "run", fail_restore)
    else:
        original_inventory = recovery.inventory
        calls = 0

        def mismatch(*args):
            nonlocal calls
            calls += 1
            result = original_inventory(*args)
            return result if calls == 1 else "mismatch"

        monkeypatch.setattr(recovery, "inventory", mismatch)
    assessed = preview(client, workspace)
    response = client.post(
        f"/api/workspaces/{workspace}/deletion/prepare",
        json={"fingerprint": assessed["fingerprint"]},
    )
    assert response.status_code == 400
    assert "private database details" not in response.text
    assert manager.approvals == {}
    assert store.run(run.id, run.workspace_id) == run
    assert list(manager.recovery_dir.glob("*.pgdump"))
    with psycopg.connect(store.database_url) as db:
        assert db.execute("SELECT datname FROM pg_database ORDER BY datname").fetchall() == before
