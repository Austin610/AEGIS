import asyncio
import multiprocessing
import os
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from test_api import create_workspace

from aegis.adapter_contract import AdapterOutput
from aegis.adapters import default_registry
from aegis.api import create_app
from aegis.durable_jobs import DurableJobManager
from aegis.jobs import JobManager
from aegis.repository import Repository
from aegis.store import Store

pytestmark = pytest.mark.durable


def test_embedded_worker_completes_api_submission(tmp_path):
    token = "durable-api-test-token-000000000000000000"
    with TestClient(
        create_app(tmp_path / "embedded.db", token, durable_jobs=True), base_url="http://127.0.0.1"
    ) as client:
        client.headers["Authorization"] = "Bearer " + token
        workspace = create_workspace(client)
        job = submit(client, workspace)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = client.app.state.repository.store
            with status.connection() as db:
                row = db.execute("SELECT status FROM jobs WHERE id=?", (job["id"],)).fetchone()
            if row[0] == "succeeded":
                break
            time.sleep(0.05)
        assert row[0] == "succeeded"
        assert len(client.app.state.repository.artifacts(UUID(workspace))) == 1


def process_claim(database, ready, start, crash=False):
    manager = DurableJobManager(Repository(Store(database)), default_registry())
    ready.put(True)
    if not start.wait(15):
        raise RuntimeError("Test start timed out")
    claim = manager.claim()
    if claim:
        if crash:
            original = manager.write_job

            def crash_before_status(db, job):
                if job.status == "succeeded":
                    os._exit(17)
                original(db, job)

            manager.write_job = crash_before_status
        asyncio.run(manager.execute_claim(*claim))


def test_process_race_and_crash_before_commit(client):
    workspace = create_workspace(client)
    repository = client.app.state.repository
    database = repository.store.database_url or str(repository.store.path)
    context = multiprocessing.get_context("spawn")

    def launch(count, crash=False):
        ready, start = context.Queue(), context.Event()
        processes = [
            context.Process(target=process_claim, args=(database, ready, start, crash))
            for _ in range(count)
        ]
        try:
            for process in processes:
                process.start()
            for _ in processes:
                assert ready.get(timeout=20) is True
            start.set()
            for process in processes:
                process.join(20)
                assert process.exitcode == (17 if crash else 0)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(5)
            ready.close()

    first = submit(client, workspace)
    launch(2)
    assert len(repository.artifacts(UUID(workspace))) == 1
    assert client.app.state.jobs.get(UUID(workspace), UUID(first["id"])).status == "succeeded"
    crashed = submit(client, workspace)
    launch(1, crash=True)
    # The process died after inserting its artifact but before writing success: both rolled back.
    assert len(repository.artifacts(UUID(workspace))) == 1
    with repository.store.connection() as db:
        db.execute(
            "UPDATE job_queue SET lease_until=? WHERE job_id=?",
            ((datetime.now(UTC) - timedelta(hours=1)).isoformat(), crashed["id"]),
        )
    client.app.state.jobs.recover()
    launch(1)
    assert len(repository.artifacts(UUID(workspace))) == 2
    assert client.app.state.jobs.get(UUID(workspace), UUID(crashed["id"])).status == "succeeded"


def submit(client, workspace, note="Durable observation"):
    response = client.post(
        f"/api/workspaces/{workspace}/jobs",
        json={
            "adapter_id": "notes",
            "target": "fixture://local/sample",
            "payload": {"title": "Queued note", "note": note},
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_queued_work_survives_new_worker_and_commits_once(client):
    workspace = create_workspace(client)
    job = submit(client, workspace)
    repository = client.app.state.repository
    first = DurableJobManager(repository, default_registry())
    second = DurableJobManager(repository, default_registry())
    first.recover()
    assert first.get(UUID(workspace), UUID(job["id"])).status == "queued"
    claim = first.claim()
    assert claim is not None
    assert second.claim() is None
    asyncio.run(first.execute_claim(*claim))
    stored = second.get(UUID(workspace), UUID(job["id"]))
    assert stored.status == "succeeded"
    # Replaying the completed attempt cannot create a second result.
    asyncio.run(first.execute_claim(*claim))
    artifacts = repository.artifacts(UUID(workspace))
    assert len(artifacts) == 1
    assert artifacts[0]["id"] == stored.result["artifact_id"]
    with repository.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM job_queue").fetchone()[0] == 0
    assert repository.events(UUID(workspace))[0]["metadata"]["actor"] == "owner"


def test_expired_attempt_is_fenced_and_retried_with_a_bound(client):
    workspace = create_workspace(client)
    job = submit(client, workspace)
    repository = client.app.state.repository
    old = DurableJobManager(repository, default_registry())
    new = DurableJobManager(repository, default_registry())
    stale = old.claim()
    assert stale is not None

    def expire():
        with repository.store.connection() as db:
            db.execute(
                "UPDATE job_queue SET lease_until=? WHERE job_id=?",
                ((datetime.now(UTC) - timedelta(hours=1)).isoformat(), job["id"]),
            )

    expire()
    new.recover()
    replacement = new.claim()
    assert replacement is not None and replacement[2] != stale[2]
    old.persist(stale[0], AdapterOutput(kind="note", data={"note": "stale"}), stale[2], stale[3])
    assert repository.artifacts(UUID(workspace)) == []
    expire()
    new.recover()
    third = new.claim()
    assert third is not None
    expire()
    new.recover()
    stored = new.get(UUID(workspace), UUID(job["id"]))
    assert stored.status == "failed" and stored.error == "worker_retry_exhausted"
    assert repository.artifacts(UUID(workspace)) == []


def test_cross_process_cancel_and_scope_revocation_prevent_results(client):
    workspace = create_workspace(client)
    repository = client.app.state.repository
    worker = DurableJobManager(repository, default_registry())
    job = submit(client, workspace)
    claim = worker.claim()
    assert claim is not None
    response = client.post(f"/api/workspaces/{workspace}/jobs/{job['id']}/cancel")
    assert response.json()["status"] == "cancelled"
    asyncio.run(worker.execute_claim(*claim))
    assert repository.artifacts(UUID(workspace)) == []
    submit(client, workspace)
    claim = worker.claim()
    assert claim is not None
    scope = repository.scope(UUID(workspace))
    repository.save_scope(scope.model_copy(update={"capabilities": ()}))
    asyncio.run(worker.execute_claim(*claim))
    assert worker.get(UUID(workspace), claim[0].id).error == "scope_no_longer_allowed"
    assert repository.artifacts(UUID(workspace)) == []


def test_result_and_status_roll_back_together_on_storage_failure(client, monkeypatch):
    workspace = create_workspace(client)
    repository = client.app.state.repository
    worker = DurableJobManager(repository, default_registry())
    job = submit(client, workspace)
    claim = worker.claim()
    assert claim is not None
    original = worker.write_job

    def fail_success(db, record):
        if record.status == "succeeded":
            raise ValueError("Simulated commit failure")
        original(db, record)

    monkeypatch.setattr(worker, "write_job", fail_success)
    asyncio.run(worker.execute_claim(*claim))
    assert repository.artifacts(UUID(workspace)) == []
    assert worker.get(UUID(workspace), UUID(job["id"])).status == "failed"


def test_queue_capacity_input_integrity_and_legacy_mode_guard(client):
    workspace = create_workspace(client)
    repository = client.app.state.repository
    worker = DurableJobManager(repository, default_registry())
    bad = client.post(
        f"/api/workspaces/{workspace}/jobs",
        json={
            "adapter_id": "notes",
            "target": "fixture://local/sample",
            "payload": {"title": "Secret", "note": "token=canary-secret"},
        },
    )
    assert bad.status_code == 400
    with repository.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM job_queue").fetchone()[0] == 0
    jobs = [submit(client, workspace) for _ in range(32)]
    with pytest.raises(ValueError, match="full"):
        asyncio.run(
            worker.submit(
                UUID(workspace),
                "notes",
                "fixture://local/sample",
                {"title": "Overflow", "note": "Overflow"},
            )
        )
    with repository.store.connection() as db:
        db.execute("UPDATE job_queue SET input='{}' WHERE job_id=?", (jobs[0]["id"],))
    assert worker.claim() is None
    assert worker.get(UUID(workspace), UUID(jobs[0]["id"])).error == "queue_validation_failed"
    with pytest.raises(ValueError, match="durable"):
        JobManager(repository, default_registry()).recover()


def test_shutdown_releases_only_own_work_and_leaves_queued_work(client):
    workspace = create_workspace(client)
    repository = client.app.state.repository
    first = DurableJobManager(repository, default_registry())
    other = DurableJobManager(repository, default_registry())
    jobs = [submit(client, workspace) for _ in range(3)]
    own = first.claim()
    foreign = other.claim()
    assert own and foreign
    asyncio.run(first.shutdown())
    assert first.get(UUID(workspace), UUID(jobs[0]["id"])).status == "queued"
    assert first.get(UUID(workspace), UUID(jobs[1]["id"])).status == "running"
    assert first.get(UUID(workspace), UUID(jobs[2]["id"])).status == "queued"
    asyncio.run(other.shutdown())
