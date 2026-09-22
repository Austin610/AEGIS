"""Disposable, loopback-only browser test server; never opens the user's database."""

import tempfile
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn

from aegis.api import create_app
from aegis.artifact_lifecycle import ArtifactLifecycle
from aegis.assurance import Check, Fixture
from aegis.policy import Scope, Workspace
from aegis.service import import_fixture

with tempfile.TemporaryDirectory(prefix="aegis-browser-") as directory:
    app = create_app(Path(directory) / "test.db", "aegis-browser-tests-only-token-000000000000",
                     durable_jobs=os.environ.get("AEGIS_BROWSER_DURABLE") == "1", deletion_grace_days=30, recovery_retention_days=30)
    repository = app.state.repository
    for index in range(55):
        repository.store.add_workspace(Workspace(name=f"Older workspace {index}"))
    workspace = Workspace(name="Browser verification")
    repository.store.add_workspace(workspace)
    now = datetime.now(UTC)
    scope = Scope(workspace_id=workspace.id, allowed_targets=("fixture://local/sample",),
                  capabilities=("fixture.evaluate", "evidence.import"),
                  starts_at=now-timedelta(days=1), expires_at=now+timedelta(days=1))
    repository.save_scope(scope)
    for index in range(55):
        repository.add_artifact(workspace.id, "note", {"note": f"Artifact {index}"})
    for index in range(3):
        fixture = Fixture(target="fixture://local/sample", target_version=f"revision-{index}",
                          policy_version="v1", identity_set="synthetic",
                          checks=(Check(id="check", title="Recorded check", expected="deny",
                                        observed="deny"),))
        run = import_fixture(repository.store, workspace.id, scope, fixture)
        aged = run.model_copy(update={"created_at": now-timedelta(days=60)})
        with repository.store.connection() as db:
            db.execute("UPDATE runs SET payload=? WHERE id=?", (aged.model_dump_json(), str(run.id)))
        if index == 0:
            repository.store.baseline(workspace.id, "protected", run.id)
    deletion_workspace = Workspace(name="Deletion browser verification")
    repository.store.add_workspace(deletion_workspace)
    deletion_scope = Scope(workspace_id=deletion_workspace.id,
                           allowed_targets=("fixture://local/sample",),
                           capabilities=("fixture.evaluate", "evidence.import"),
                           starts_at=now-timedelta(days=1), expires_at=now+timedelta(days=1))
    repository.save_scope(deletion_scope)
    deletion_run = import_fixture(repository.store, deletion_workspace.id, deletion_scope, fixture)
    repository.archive(deletion_workspace.id, deletion_run.id, True)
    with repository.store.connection() as db:
        db.execute("UPDATE archived_runs SET archived_at=? WHERE run_id=?",
                   ((now-timedelta(days=40)).isoformat(), str(deletion_run.id)))
    artifact = repository.add_artifact(deletion_workspace.id, "note", {"note": "Disposable deletion artifact"})
    from uuid import UUID
    import json
    ArtifactLifecycle(repository.store).archive(deletion_workspace.id, UUID(artifact["id"]), True)
    with repository.store.connection() as db:
        db.execute("UPDATE workflow_records SET payload=? WHERE kind='artifact_archive'",
                   (json.dumps({"archived_at": (now-timedelta(days=40)).isoformat()}),))
    from aegis.backup import snapshot, restore
    from uuid import uuid4
    recovery = app.state.deletion.retention
    recovery.directory.mkdir(exist_ok=True)
    for index in range(2):
        identity = uuid4().hex
        backup_path = recovery.directory / (identity + ".backup.db")
        restored_path = recovery.directory / (identity + ".restored.db")
        backup = snapshot(repository.store.path, backup_path)
        restore(backup_path, restored_path, backup["sha256"])
        recovery.register([backup_path, restored_path], backup["sha256"], "browser-fixture")
        if index == 0:
            with repository.store.connection() as db:
                db.execute("UPDATE audit_events SET created_at=? WHERE action='recovery.verified'",
                           ((now-timedelta(days=40)).isoformat(),))
    uvicorn.run(app, host="127.0.0.1", port=8878, access_log=False)
