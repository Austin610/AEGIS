import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from test_api import create_workspace

from aegis.assurance import Check, Fixture
from aegis.backup import validate
from aegis.jobs import Job
from aegis.service import import_fixture


def archived_run(client, workspace):
    repository = client.app.state.repository
    scope = repository.scope(UUID(workspace))
    fixture = Fixture(
        target="fixture://local/sample",
        target_version="v1",
        policy_version="p1",
        identity_set="test",
        checks=(Check(id="one", title="One", expected="deny", observed="deny"),),
    )
    run = import_fixture(repository.store, UUID(workspace), scope, fixture)
    repository.archive(UUID(workspace), run.id, True)
    with repository.store.connection() as db:
        db.execute(
            "UPDATE archived_runs SET archived_at=? WHERE run_id=?",
            ((datetime.now(UTC) - timedelta(days=40)).isoformat(), str(run.id)),
        )
    return run


def preview(client, workspace):
    response = client.post(f"/api/workspaces/{workspace}/deletion/preview", json={"grace_days": 30})
    assert response.status_code == 200, response.text
    return response.json()


def prepare(client, workspace):
    if client.app.state.repository.store.database_url:
        bin_dir = os.environ.get("AEGIS_TEST_POSTGRES_BIN")
        if not bin_dir:
            pytest.skip("Native PostgreSQL recovery tools not configured")
        client.app.state.deletion.pg_bin = Path(bin_dir)
        client.app.state.deletion.recovery_dir = client.app.state.test_directory / "recovery"
    client.app.state.deletion.grace_days = 30
    assessed = preview(client, workspace)
    response = client.post(
        f"/api/workspaces/{workspace}/deletion/prepare",
        json={"fingerprint": assessed["fingerprint"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def apply(client, workspace, approval, **kwargs):
    return client.post(
        f"/api/workspaces/{workspace}/deletion/apply",
        json={k: approval[k] for k in ("token", "confirmation")},
        **kwargs,
    )


def test_confirmed_deletion_tombstone_replay_cache_and_restore(client):
    workspace = create_workspace(client)
    run = archived_run(client, workspace)
    root = f"/api/workspaces/{workspace}"
    cursor = client.get(root + "/snapshots/runs?archived=true").json()["cursor"]
    approval = prepare(client, workspace)
    assert apply(client, workspace, approval).json()["deleted_runs"] == 1
    assert apply(client, workspace, approval).status_code == 400
    assert (
        client.get(
            root + "/snapshots/runs", params={"archived": True, "cursor": cursor}
        ).status_code
        == 400
    )
    store = client.app.state.repository.store
    with store.connection() as db:
        assert db.execute("SELECT 1 FROM runs WHERE id=?", (str(run.id),)).fetchone() is None
        assert (
            db.execute("SELECT 1 FROM evidence WHERE id=?", (str(run.evidence_id),)).fetchone()
            is None
        )
        if not store.database_url:
            validate(db)
        raw = db.execute(
            "SELECT payload FROM workflow_records WHERE kind='deletion_tombstone'"
        ).fetchone()[0]
        assert str(run.id) in raw and '"checks"' not in raw
        db.execute(
            "UPDATE workflow_records SET payload=? WHERE kind='deletion_tombstone'",
            (raw.replace('"actor":', '"changed_actor":'),),
        )
        if not store.database_url:
            with pytest.raises(ValueError, match="tombstone"):
                validate(db)
    if store.database_url:
        assert list(client.app.state.deletion.recovery_dir.glob("*.pgdump"))
        return
    restored = next((store.path.parent / "deletion-recovery").glob("*.restored.db"))
    with sqlite3.connect(restored) as db:
        assert db.execute("SELECT 1 FROM runs WHERE id=?", (str(run.id),)).fetchone()
        validate(db)


@pytest.mark.parametrize(
    "change", ["baseline", "reference", "hold", "active_job", "unarchive", "policy"]
)
def test_changed_dependencies_reject_apply(client, change):
    workspace = create_workspace(client)
    run = archived_run(client, workspace)
    approval = prepare(client, workspace)
    repository = client.app.state.repository
    with repository.store.connection() as db:
        if change == "baseline":
            db.execute("INSERT INTO baselines VALUES (?,?,?)", (workspace, "new", str(run.id)))
        elif change in ("reference", "hold"):
            db.execute(
                "INSERT INTO workflow_records VALUES (?,?,?,?)",
                (
                    workspace,
                    "item" if change == "reference" else "hold",
                    "new",
                    json.dumps({"run_id": str(run.id)}),
                ),
            )
        elif change == "active_job":
            job = Job(
                workspace_id=UUID(workspace), adapter_id="notes", target="fixture://local/sample"
            )
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?)",
                (str(job.id), workspace, "queued", job.model_dump_json()),
            )
        elif change == "unarchive":
            db.execute("DELETE FROM archived_runs WHERE run_id=?", (str(run.id),))
        else:
            db.execute("INSERT INTO workspace_policies VALUES (?,?)", (workspace, "{}"))
    assert apply(client, workspace, approval).status_code == 400
    assert repository.store.run(run.id, UUID(workspace)) == run


def test_confirmation_actor_workspace_expiry_and_rollback(client, monkeypatch):
    workspace, other = create_workspace(client), create_workspace(client)
    run = archived_run(client, workspace)
    approval = prepare(client, workspace)
    assert apply(client, other, approval).status_code == 400
    assert apply(client, workspace, {**approval, "confirmation": "yes"}).status_code == 400
    for role in ("reader", "analyst", "admin"):
        key = client.post(
            "/api/access-keys",
            json={
                "label": role,
                "role": role,
                "workspace_ids": [] if role == "admin" else [workspace],
            },
        ).json()["token"]
        assert apply(
            client, workspace, approval, headers={"Authorization": "Bearer " + key}
        ).status_code == (400 if role == "admin" else 403)
        if role != "admin":
            assert (
                client.post(
                    f"/api/workspaces/{workspace}/deletion/prepare",
                    json={"fingerprint": "a" * 64},
                    headers={"Authorization": "Bearer " + key},
                ).status_code
                == 403
            )
    from aegis.repository import Repository

    original = Repository.audit

    def fail_audit(self, *args, **kwargs):
        raise ValueError("Injected audit failure")

    monkeypatch.setattr(Repository, "audit", fail_audit)
    assert apply(client, workspace, approval).status_code == 400
    assert client.app.state.repository.store.run(run.id, UUID(workspace)) == run
    with client.app.state.repository.store.connection() as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM workflow_records WHERE kind='deletion_tombstone'"
            ).fetchone()[0]
            == 0
        )
    monkeypatch.setattr(Repository, "audit", original)
    client.app.state.deletion.approvals[approval["token"]]["deadline"] = 0
    assert apply(client, workspace, approval).status_code == 400


def test_disabled_prepare_never_deletes(client):
    workspace = create_workspace(client)
    run = archived_run(client, workspace)
    result = preview(client, workspace)
    assert (
        client.post(
            f"/api/workspaces/{workspace}/deletion/prepare",
            json={"fingerprint": result["fingerprint"]},
        ).status_code
        == 400
    )
    assert client.app.state.repository.store.run(run.id, UUID(workspace)) == run


def test_recovery_failure_cannot_issue_approval(client, monkeypatch):
    workspace = create_workspace(client)
    run = archived_run(client, workspace)
    if client.app.state.repository.store.database_url:
        pytest.skip("SQLite recovery only")
    client.app.state.deletion.grace_days = 30

    def fail_restore(*args, **kwargs):
        raise ValueError("Injected restore verification failure")

    monkeypatch.setattr("aegis.deletion.restore", fail_restore)
    result = preview(client, workspace)
    assert (
        client.post(
            f"/api/workspaces/{workspace}/deletion/prepare",
            json={"fingerprint": result["fingerprint"]},
        ).status_code
        == 400
    )
    assert client.app.state.deletion.approvals == {}
    assert client.app.state.repository.store.run(run.id, UUID(workspace)) == run


def test_grouped_finding_history_is_protected(client):
    workspace = create_workspace(client)
    archived_run(client, workspace)
    with client.app.state.repository.store.connection() as db:
        db.execute(
            "INSERT INTO workflow_records VALUES (?,?,?,?)",
            (workspace, "item", "bug", json.dumps({"finding_id": "grouped-finding-key"})),
        )
    result = preview(client, workspace)
    assert result["eligible_count"] == 0
    assert "linked_workflow_history" in result["records"][0]["blockers"]


def test_coverage_cannot_link_run_deleted_after_initial_validation(client, monkeypatch):
    from aegis.deletion import ApplyDeletion
    from aegis.workflow import Assessment, Workflow

    workspace = create_workspace(client)
    archived_run(client, workspace)
    approval = prepare(client, workspace)
    repository = client.app.state.repository
    manager = client.app.state.deletion
    original = repository.store.run

    def delete_between_validation_and_write(*args, **kwargs):
        value = original(*args, **kwargs)
        actor = manager.approvals[approval["token"]]["actor"]
        manager.apply(
            UUID(workspace),
            actor,
            ApplyDeletion(token=approval["token"], confirmation=approval["confirmation"]),
        )
        return value

    monkeypatch.setattr(repository.store, "run", delete_between_validation_and_write)
    value = Assessment(
        revision=0,
        framework="test",
        version="1",
        control="one",
        title="One",
        status="Passed",
        evidence_run_id=UUID(approval["records"][0]["run_id"]),
    )
    with pytest.raises(ValueError, match="no longer exists"):
        Workflow(repository).save(UUID(workspace), "coverage", "one", value)
    with repository.store.connection() as db:
        assert (
            db.execute("SELECT COUNT(*) FROM workflow_records WHERE kind='coverage'").fetchone()[0]
            == 0
        )


def test_assessment_is_read_only_and_baseline_changes_fingerprint(client):
    workspace = create_workspace(client)
    run = archived_run(client, workspace)
    result = preview(client, workspace)
    assert result["eligible_count"] == 1
    assert result["apply_enabled"] is False
    repository = client.app.state.repository
    assert repository.store.run(run.id, UUID(workspace)) == run
    assert preview(client, workspace)["fingerprint"] == result["fingerprint"]
    repository.store.baseline(UUID(workspace), "keep", run.id)
    changed = preview(client, workspace)
    assert changed["eligible_count"] == 0
    assert "baseline" in changed["records"][0]["blockers"]
    assert changed["fingerprint"] != result["fingerprint"]
    assert (
        client.post(f"/api/workspaces/{workspace}/deletion/apply", json=result).status_code == 422
    )


def test_references_holds_jobs_integrity_and_grace_block_candidates(client):
    workspace = create_workspace(client)
    run = archived_run(client, workspace)
    repository = client.app.state.repository
    with repository.store.connection() as db:
        db.execute(
            "INSERT INTO workflow_records VALUES (?,?,?,?)",
            (workspace, "item", "linked", json.dumps({"source": str(run.id)})),
        )
        db.execute(
            "INSERT INTO workflow_records VALUES (?,?,?,?)", (workspace, "legal_hold", "hold", "{}")
        )
        job = Job(workspace_id=UUID(workspace), adapter_id="notes", target="fixture://local/sample")
        db.execute(
            "INSERT INTO jobs VALUES (?,?,?,?)",
            (str(job.id), workspace, "queued", job.model_dump_json()),
        )
        db.execute("UPDATE evidence SET payload='{}' WHERE id=?", (str(run.evidence_id),))
        db.execute(
            "UPDATE archived_runs SET archived_at=? WHERE run_id=?",
            (datetime.now(UTC).isoformat(), str(run.id)),
        )
    result = preview(client, workspace)
    assert set(result["records"][0]["blockers"]) == {
        "referenced_record",
        "workspace_hold",
        "active_workspace_jobs",
        "evidence_integrity",
        "archive_grace_period",
    }
    assert result["records"][0]["references"] == [{"table": "workflow_records", "id": "linked"}]


def test_assessment_is_admin_only_and_workspace_scoped(client):
    workspace, other = create_workspace(client), create_workspace(client)
    archived_run(client, other)
    assert preview(client, workspace)["records"] == []
    assert len(preview(client, other)["records"]) == 1
    for role in ("reader", "analyst"):
        key = client.post(
            "/api/access-keys",
            json={
                "label": role,
                "role": role,
                "workspace_ids": [workspace, other],
            },
        ).json()["token"]
        assert (
            client.post(
                f"/api/workspaces/{other}/deletion/preview",
                json={},
                headers={"Authorization": "Bearer " + key},
            ).status_code
            == 403
        )
    assert client.post(f"/api/workspaces/{uuid4()}/deletion/preview", json={}).status_code == 400
    assert (
        client.post(
            f"/api/workspaces/{workspace}/deletion/preview", json={"grace_days": 0}
        ).status_code
        == 422
    )
