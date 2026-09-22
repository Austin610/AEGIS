import json
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from test_api import create_workspace

from aegis.backup import checksum
from aegis.repository import Repository


def managed_copies(client):
    manager = client.app.state.deletion
    if manager.store.database_url:
        manager.recovery_dir = client.app.state.test_directory / "recovery"
    retention = manager.retention
    retention.days = 30
    root = retention.directory
    root.mkdir(exist_ok=True)
    groups = []
    for index in range(2):
        identity = uuid4().hex
        files = [root / (identity + suffix) for suffix in (".backup.db", ".restored.db")]
        for path in files:
            path.write_bytes(f"Disposable recovery-copy test data {index}".encode())
        retention.register(files, checksum(files[0]), "test")
        if index == 0:
            with manager.store.connection() as db:
                db.execute(
                    "UPDATE audit_events SET created_at=? WHERE action='recovery.verified'",
                    ((datetime.now(UTC) - timedelta(days=40)).isoformat(),),
                )
        groups.append(files)
    return retention, groups


def approval(client, name):
    preview = client.get("/api/recovery/expiry").json()
    response = client.post(
        "/api/recovery/expiry/prepare",
        json={
            "name": name,
            "fingerprint": preview["fingerprint"],
        },
    )
    assert response.status_code == 200, response.text
    return {k: response.json()[k] for k in ("token", "confirmation")}


def test_expire_one_managed_copy_preserves_latest_and_unmanaged(client):
    retention, groups = managed_copies(client)
    unmanaged = retention.directory / "personal-backup.db"
    unmanaged.write_bytes(b"Never manage this copy")
    preview = client.get("/api/recovery/expiry").json()
    assert sum(r["eligible"] for r in preview["records"]) == 2
    approved = approval(client, groups[0][0].name)
    assert (
        client.post("/api/recovery/expiry/apply", json=approved).json()["status"] == "file_removed"
    )
    assert not groups[0][0].exists()
    assert groups[0][1].exists() and all(p.exists() for p in groups[1]) and unmanaged.exists()
    assert client.post("/api/recovery/expiry/apply", json=approved).status_code == 400
    with retention.manager.store.connection() as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM audit_events WHERE action='recovery.expiry.completed'"
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("change", ["contents", "policy", "hold", "active_approval", "newer_copy"])
def test_changed_expiry_protections_reject_apply(client, change):
    retention, groups = managed_copies(client)
    approved = approval(client, groups[0][0].name)
    if change == "contents":
        groups[0][0].write_bytes(b"Changed content")
    elif change == "policy":
        retention.days = None
    elif change == "hold":
        workspace = create_workspace(client)
        with retention.manager.store.connection() as db:
            db.execute(
                "INSERT INTO workflow_records(workspace_id,kind,identity,payload) VALUES (?,?,?,?)",
                (workspace, "legal_hold", "keep", "{}"),
            )
    elif change == "active_approval":
        retention.manager.approvals["active"] = {
            "backup_sha256": checksum(groups[0][0]),
            "deadline": time.monotonic() + 300,
        }
    else:
        path = retention.directory / (uuid4().hex + ".pgdump")
        path.write_bytes(b"New verified test copy")
        retention.register([path], checksum(path), "test")
    assert client.post("/api/recovery/expiry/apply", json=approved).status_code == 400
    assert all(p.exists() for p in groups[0])


def test_expiry_permissions_actor_expiration_and_confirmation(client):
    retention, groups = managed_copies(client)
    approved = approval(client, groups[0][0].name)
    assert (
        client.post(
            "/api/recovery/expiry/apply", json={**approved, "confirmation": "yes"}
        ).status_code
        == 400
    )
    workspace = create_workspace(client)
    for role in ("reader", "analyst", "admin"):
        token = client.post(
            "/api/access-keys",
            json={
                "label": role,
                "role": role,
                "workspace_ids": [] if role == "admin" else [workspace],
            },
        ).json()["token"]
        headers = {"Authorization": "Bearer " + token}
        assert client.post(
            "/api/recovery/expiry/apply", json=approved, headers=headers
        ).status_code == (400 if role == "admin" else 403)
        if role != "admin":
            assert client.get("/api/recovery/expiry", headers=headers).status_code == 403
    retention.tokens[approved["token"]]["deadline"] = 0
    assert client.post("/api/recovery/expiry/apply", json=approved).status_code == 400
    assert groups[0][0].exists()


def test_missing_complete_recovery_and_changed_path_fail_closed(client):
    retention, groups = managed_copies(client)
    for files in groups:
        files[0].unlink()
    assert not any(r["eligible"] for r in client.get("/api/recovery/expiry").json()["records"])
    with retention.manager.store.connection() as db:
        row = db.execute(
            "SELECT id,payload FROM audit_events WHERE action='recovery.verified' LIMIT 1"
        ).fetchone()
        metadata = json.loads(row[1])
        metadata["files"][0]["name"] = "../outside.db"
        db.execute("UPDATE audit_events SET payload=? WHERE id=?", (json.dumps(metadata), row[0]))
    assert client.get("/api/recovery/expiry").status_code == 400
    assert groups[0][1].exists()


def test_final_audit_failure_is_reconciled_without_false_rollback(client, monkeypatch):
    retention, groups = managed_copies(client)
    approved = approval(client, groups[0][0].name)
    original = Repository.audit

    def fail_final(self, workspace, action, metadata):
        if action == "recovery.expiry.completed":
            raise ValueError("Injected final audit failure")
        return original(self, workspace, action, metadata)

    monkeypatch.setattr(Repository, "audit", fail_final)
    assert (
        client.post("/api/recovery/expiry/apply", json=approved).json()["status"]
        == "file_removed_audit_pending"
    )
    assert not groups[0][0].exists()
    monkeypatch.setattr(Repository, "audit", original)
    # A fresh service has no in-memory approvals; persisted intent still reconciles.
    from aegis.recovery_retention import RecoveryRetention

    restarted = RecoveryRetention(retention.manager, 30)
    restarted.preview()
    with retention.manager.store.connection() as db:
        raw = db.execute(
            "SELECT payload FROM audit_events WHERE action='recovery.expiry.completed'"
        ).fetchone()[0]
        assert json.loads(raw)["outcome"] == "file_absent_on_reconciliation"


def test_failed_unlink_preserves_copy_and_reconciles(client, monkeypatch):
    from pathlib import Path

    retention, groups = managed_copies(client)
    approved = approval(client, groups[0][0].name)
    original = Path.unlink

    def fail_unlink(path, *args, **kwargs):
        if path == groups[0][0]:
            raise ValueError("Injected file removal failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    assert client.post("/api/recovery/expiry/apply", json=approved).status_code == 400
    assert groups[0][0].exists()
    assert client.get("/api/recovery/expiry").status_code == 200
    with retention.manager.store.connection() as db:
        raw = db.execute(
            "SELECT payload FROM audit_events WHERE action='recovery.expiry.completed'"
        ).fetchone()[0]
        assert json.loads(raw)["outcome"] == "file_present_requires_new_review"


def test_disabled_expiry_and_duplicate_receipts_fail_closed(client):
    retention, groups = managed_copies(client)
    retention.days = None
    assert not any(r["eligible"] for r in client.get("/api/recovery/expiry").json()["records"])
    retention.days = 30
    retention.register(groups[0], checksum(groups[0][0]), "test")
    assert client.get("/api/recovery/expiry").status_code == 400
    assert all(path.exists() for group in groups for path in group)
