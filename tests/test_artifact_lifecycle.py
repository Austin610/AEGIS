import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from test_api import create_workspace


def setup(client):
    workspace = create_workspace(client)
    repo = client.app.state.repository
    artifact = repo.add_artifact(UUID(workspace), "note", {"note": "Disposable artifact"})
    root = f"/api/workspaces/{workspace}"
    assert (
        client.put(
            root + f"/artifacts/{artifact['id']}/archive", json={"archived": True}
        ).status_code
        == 200
    )
    with repo.store.connection() as db:
        db.execute(
            "UPDATE workflow_records SET payload=? WHERE kind='artifact_archive'",
            (json.dumps({"archived_at": (datetime.now(UTC) - timedelta(days=40)).isoformat()}),),
        )
    return workspace, artifact, root


def preview(client, root):
    response = client.post(root + "/deletion/preview", json={"resource": "artifacts"})
    assert response.status_code == 200, response.text
    return response.json()


def prepare(client, root):
    manager = client.app.state.deletion
    manager.grace_days = 30
    if manager.store.database_url:
        if not os.environ.get("AEGIS_TEST_POSTGRES_BIN"):
            pytest.skip("Native recovery tooling required")
        manager.pg_bin = Path(os.environ["AEGIS_TEST_POSTGRES_BIN"])
        manager.recovery_dir = client.app.state.test_directory / "recovery"
    response = client.post(
        root + "/deletion/prepare",
        json={
            "resource": "artifacts",
            "fingerprint": preview(client, root)["fingerprint"],
        },
    )
    assert response.status_code == 200, response.text
    return {key: response.json()[key] for key in ("token", "confirmation")}


def test_artifact_archive_restore_and_confirmed_deletion(client):
    workspace, artifact, root = setup(client)
    assert preview(client, root)["eligible_count"] == 1
    # Repeated archival must not reset the grace clock.
    client.put(root + f"/artifacts/{artifact['id']}/archive", json={"archived": True})
    assert preview(client, root)["eligible_count"] == 1
    approval = prepare(client, root)
    assert approval["confirmation"] == "DELETE 1 ARCHIVED ARTIFACTS"
    assert client.post(root + "/deletion/apply", json=approval).json()["deleted_artifacts"] == 1
    assert client.post(root + "/deletion/apply", json=approval).status_code == 400
    assert client.app.state.repository.artifacts(UUID(workspace)) == []
    with client.app.state.repository.store.connection() as db:
        tombstone = db.execute(
            "SELECT payload FROM workflow_records WHERE kind='deletion_tombstone'"
        ).fetchone()[0]
        assert artifact["id"] in tombstone and "Disposable artifact" not in tombstone


@pytest.mark.parametrize("change", ["reference", "history", "hold", "restore", "corrupt"])
def test_artifact_dependency_changes_reject_apply(client, change):
    workspace, artifact, root = setup(client)
    approval = prepare(client, root)
    with client.app.state.repository.store.connection() as db:
        if change in ("reference", "history", "hold"):
            payload = {
                "reference": {"source": artifact["id"]},
                "history": {"finding_id": "grouped-sarif-finding"},
                "hold": {},
            }[change]
            db.execute(
                "INSERT INTO workflow_records(workspace_id,kind,identity,payload) VALUES (?,?,?,?)",
                (
                    workspace,
                    "hold" if change == "hold" else "item",
                    "protected",
                    json.dumps(payload),
                ),
            )
        elif change == "restore":
            db.execute(
                "DELETE FROM workflow_records WHERE kind='artifact_archive' AND workspace_id=?",
                (workspace,),
            )
        else:
            db.execute("UPDATE artifacts SET payload='{}' WHERE id=?", (artifact["id"],))
    assert client.post(root + "/deletion/apply", json=approval).status_code == 400
    with client.app.state.repository.store.connection() as db:
        assert db.execute("SELECT 1 FROM artifacts WHERE id=?", (artifact["id"],)).fetchone()


def test_artifact_archive_permissions_scope_and_grace(client):
    workspace, artifact, root = setup(client)
    other = create_workspace(client)
    assert (
        client.put(
            f"/api/workspaces/{other}/artifacts/{artifact['id']}/archive", json={"archived": True}
        ).status_code
        == 400
    )
    key = client.post(
        "/api/access-keys",
        json={"label": "analyst", "role": "analyst", "workspace_ids": [workspace]},
    ).json()["token"]
    assert (
        client.put(
            root + f"/artifacts/{artifact['id']}/archive",
            json={"archived": False},
            headers={"Authorization": "Bearer " + key},
        ).status_code
        == 403
    )
    client.put(root + f"/artifacts/{artifact['id']}/archive", json={"archived": False})
    assert preview(client, root)["records"] == []
    client.put(root + f"/artifacts/{artifact['id']}/archive", json={"archived": True})
    assert preview(client, root)["records"][0]["blockers"] == ["archive_grace_period"]


def test_artifact_rollback_restores_payload_and_archive_marker(client, monkeypatch):
    from aegis.repository import Repository

    workspace, artifact, root = setup(client)
    approval = prepare(client, root)

    def fail_audit(*args, **kwargs):
        raise ValueError("Injected commit failure")

    monkeypatch.setattr(Repository, "audit", fail_audit)
    assert client.post(root + "/deletion/apply", json=approval).status_code == 400
    assert client.app.state.repository.artifacts(UUID(workspace))[0]["id"] == artifact["id"]
    assert preview(client, root)["eligible_count"] == 1


def test_artifact_selection_cannot_be_prepared_as_runs(client):
    _, _, root = setup(client)
    client.app.state.deletion.grace_days = 30
    response = client.post(
        root + "/deletion/prepare",
        json={
            "fingerprint": preview(client, root)["fingerprint"],
            "resource": "runs",
        },
    )
    assert response.status_code == 400
