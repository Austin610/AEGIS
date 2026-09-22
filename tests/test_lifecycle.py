from datetime import UTC, datetime, timedelta
from uuid import UUID

from test_api import create_workspace
from test_workflow import observe

from aegis.data_policy import DataPolicies, DataPolicy


def test_scheduled_retention_opt_in_baseline_protection_and_interval(client):
    workspace = create_workspace(client)
    root = f"/api/workspaces/{workspace}"
    run_id = observe(client, workspace, "deny")
    protected = observe(client, workspace, "deny")
    repo = client.app.state.repository
    now = datetime.now(UTC)
    for identity in (run_id, protected):
        run = repo.store.run(UUID(identity), UUID(workspace)).model_copy(
            update={"created_at": now - timedelta(days=40)}
        )
        with repo.store.connection() as db:
            db.execute("UPDATE runs SET payload=? WHERE id=?", (run.model_dump_json(), identity))
    repo.store.baseline(UUID(workspace), "protected", UUID(protected))
    policies = DataPolicies(repo.store)
    policies.save(UUID(workspace), DataPolicy(archive_after_days=30), "test")
    assert policies.scheduled(now) == 0
    policies.save(
        UUID(workspace), DataPolicy(archive_after_days=30, schedule_interval_hours=24), "test"
    )
    assert policies.scheduled(now) == 1
    assert policies.scheduled(now + timedelta(hours=1)) == 0
    active = client.get(root + "/runs").json()
    assert [run["id"] for run in active] == [protected]
    assert policies.scheduled(now + timedelta(days=1)) == 0
    actions = [e["action"] for e in repo.events(UUID(workspace))]
    assert actions.count("retention.scheduled") == 2


def test_snapshot_pages_stay_fixed_and_are_scoped(client):
    workspace, other = create_workspace(client), create_workspace(client)
    root = f"/api/workspaces/{workspace}"
    repo = client.app.state.repository
    for i in range(3):
        repo.add_artifact(UUID(workspace), "note", {"note": str(i)})
    first = client.get(root + "/snapshots/artifacts?limit=2").json()
    repo.add_artifact(UUID(workspace), "note", {"note": "new"})
    second = client.get(
        root + "/snapshots/artifacts", params={"cursor": first["cursor"], "offset": 2}
    ).json()
    assert second["total"] == 3
    assert second["items"][0]["data"]["note"] == "0"
    assert client.get(root + "/snapshots/artifacts").json()["total"] == 4
    assert (
        client.get(
            f"/api/workspaces/{other}/snapshots/artifacts", params={"cursor": first["cursor"]}
        ).status_code
        == 400
    )
    key = client.post(
        "/api/access-keys", json={"label": "reader", "role": "reader", "workspace_ids": [workspace]}
    ).json()["token"]
    assert (
        client.get(
            root + "/snapshots/artifacts",
            params={"cursor": first["cursor"]},
            headers={"Authorization": "Bearer " + key},
        ).status_code
        == 400
    )
