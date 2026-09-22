import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient


def create_workspace(client: TestClient, mode: str = "appsec") -> str:
    response = client.post("/api/workspaces", json={"name": "Integration workspace", "mode": mode})
    assert response.status_code == 201, response.text
    identity = response.json()["id"]
    now = datetime.now(UTC)
    response = client.put(
        f"/api/workspaces/{identity}/scope",
        json={
            "workspace_id": identity,
            "allowed_targets": ["fixture://local/sample"],
            "capabilities": ["fixture.evaluate", "evidence.import"],
            "starts_at": (now - timedelta(minutes=1)).isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
        },
    )
    assert response.status_code == 200
    return identity


def wait_job(client: TestClient, workspace: str, identity: str) -> dict:
    for _ in range(100):
        jobs = client.get(f"/api/workspaces/{workspace}/jobs").json()
        job = next(job for job in jobs if job["id"] == identity)
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.01)
    raise AssertionError("Job did not finish")


def test_authentication_headers_and_sanitized_errors(client: TestClient) -> None:
    assert client.get("/health").status_code == 200
    token = client.headers.pop("Authorization")[7:]
    assert client.get("/api/workspaces").status_code == 401
    assert client.get("/api/schema").status_code == 401
    assert client.post("/api/session", json={"token": "secret-canary"}).status_code == 422
    response = client.post("/api/session", json={"token": token})
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert client.get("/api/workspaces").status_code == 200
    assert client.post("/api/workspaces", json={"name": "test"}).status_code == 403
    client.headers["X-Aegis-Request"] = "1"
    response = client.post("/api/workspaces", json={"name": "test", "secret": "secret-canary"})
    assert response.status_code == 422
    assert "secret-canary" not in response.text
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert client.delete("/api/session").status_code == 200
    assert client.get("/api/workspaces").status_code == 401


def test_host_origin_and_size_limits(client: TestClient) -> None:
    assert client.get("/health", headers={"Host": "other.invalid"}).status_code == 400
    assert (
        client.get("/api/workspaces", headers={"Origin": "https://other.invalid"}).status_code
        == 403
    )
    response = client.post(
        "/api/session", content="x" * 1_048_577, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413


def test_fixture_job_report_baseline_and_retention(client: TestClient) -> None:
    workspace = create_workspace(client)
    root = f"/api/workspaces/{workspace}"
    payload = {
        "target": "fixture://local/sample",
        "target_version": "v1",
        "policy_version": "p1",
        "identity_set": "alice-bob",
        "checks": [
            {
                "id": "ownership",
                "title": "Ownership",
                "expected": "deny",
                "observed": "deny",
                "severity": "high",
            }
        ],
    }

    def submit() -> dict:
        response = client.post(
            root + "/jobs",
            json={"adapter_id": "fixture", "target": "fixture://local/sample", "payload": payload},
        )
        assert response.status_code == 202, response.text
        job = wait_job(client, workspace, response.json()["id"])
        assert job["status"] == "succeeded", job
        return job

    first = submit()["result"]["run_id"]
    assert (
        client.post(root + "/baselines", json={"name": "secure", "run_id": first}).status_code
        == 201
    )
    payload["checks"][0]["observed"] = "allow"
    payload["target_version"] = "v2"
    failed = submit()["result"]["run_id"]
    assert client.get(root + f"/compare?baseline=secure&run_id={failed}").json() == {
        "ownership": "new_regression"
    }
    gate = client.post(root + f"/gate?baseline=secure&run_id={failed}", json={})
    assert gate.status_code == 200
    assert gate.json()["status"] == "fail"
    assert gate.json()["exit_code"] == 1
    assert gate.json()["blocking_checks"] == ["ownership"]
    assert (
        client.post(root + f"/gate?baseline=secure&run_id={first}", json={}).json()["status"]
        == "pass"
    )
    assert "gate.evaluated" in [event["action"] for event in client.get(root + "/audit").json()]
    assert client.get(root + f"/runs/{failed}/evidence").status_code == 200
    assert "FAIL" in client.get(root + f"/runs/{failed}/report").text
    assert client.put(root + f"/runs/{failed}/archive", json={"archived": True}).status_code == 200
    assert len(client.get(root + "/runs").json()) == 1
    assert len(client.get(root + "/runs?archived=true").json()) == 1
    assert client.put(root + f"/runs/{failed}/archive", json={"archived": False}).status_code == 200
    assert len(client.get(root + "/runs").json()) == 2
    page_one = client.get(root + "/runs?limit=1&offset=0").json()
    page_two = client.get(root + "/runs?limit=1&offset=1").json()
    assert [page_one[0]["id"], page_two[0]["id"]] == [failed, first]
    assert client.get(root + "/runs?offset=2").json() == []
    other = create_workspace(client)
    assert (
        client.post(
            f"/api/workspaces/{other}/gate?baseline=secure&run_id={failed}", json={}
        ).status_code
        == 400
    )
    assert client.get(f"/api/workspaces/{other}/runs/{failed}/evidence").status_code == 400
    assert "run.restored" in [event["action"] for event in client.get(root + "/audit").json()]


@pytest.mark.parametrize(
    "mode",
    [
        "appsec",
        "qa",
        "api_security",
        "research",
        "forensics",
        "reverse",
        "ctf",
        "bug_bounty",
        "training",
    ],
)
def test_every_mode_has_notebook_workflow(client: TestClient, mode: str) -> None:
    workspace = create_workspace(client, mode)
    response = client.post(
        f"/api/workspaces/{workspace}/jobs",
        json={
            "adapter_id": "notes",
            "target": "fixture://local/sample",
            "payload": {"title": "Review", "note": "Manual observation token=secret-canary"},
        },
    )
    assert response.status_code == 202
    job = wait_job(client, workspace, response.json()["id"])
    assert job["status"] == "succeeded"
    artifacts = client.get(f"/api/workspaces/{workspace}/artifacts").json()
    assert len(artifacts) == 1
    assert "secret-canary" not in str(artifacts)
    assert artifacts[0]["data"]["scope_decision_id"]


def test_scope_and_mode_denials_are_audited(client: TestClient) -> None:
    workspace = create_workspace(client, "forensics")
    root = f"/api/workspaces/{workspace}"
    body = {"adapter_id": "fixture", "target": "fixture://local/sample", "payload": {}}
    assert client.post(root + "/jobs", json=body).status_code == 400
    body.update(adapter_id="notes", target="fixture://outside")
    assert client.post(root + "/jobs", json=body).status_code == 400
    events = [e["action"] for e in client.get(root + "/audit").json()]
    assert "job.mode_denied" in events
    assert "job.scope_denied" in events


def test_invalid_adapter_payload_records_failure_without_input(client: TestClient) -> None:
    workspace = create_workspace(client)
    response = client.post(
        f"/api/workspaces/{workspace}/jobs",
        json={
            "adapter_id": "notes",
            "target": "fixture://local/sample",
            "payload": {"title": "secret-canary"},
        },
    )
    job = wait_job(client, workspace, response.json()["id"])
    assert job["status"] == "failed"
    assert job["error"] == "adapter_or_storage_error"
    assert "secret-canary" not in str(job)


@pytest.mark.parametrize("resource", ["", "/runs", "/jobs", "/artifacts", "/audit", "/baselines"])
@pytest.mark.parametrize("query", ["limit=0", "limit=201", "offset=-1", "offset=nope"])
def test_invalid_page_requests(client: TestClient, resource: str, query: str) -> None:
    workspace = create_workspace(client)
    path = f"/api/workspaces/{workspace}{resource}" if resource else "/api/workspaces"
    assert client.get(path + "?" + query).status_code == 422


def test_pages_reach_older_records_without_crossing_workspaces(client: TestClient) -> None:
    from uuid import UUID

    from aegis.jobs import Job
    from aegis.policy import Workspace

    repository = client.app.state.repository
    manager = client.app.state.jobs
    workspace = Workspace(name="Paged workspace")
    other = Workspace(name="Other workspace")
    repository.store.add_workspace(workspace)
    repository.store.add_workspace(other)
    expected_artifacts = []
    expected_jobs = []
    for index in range(205):
        repository.store.add_workspace(Workspace(name=f"Workspace {index}"))
        artifact = repository.add_artifact(workspace.id, "note", {"index": index})
        expected_artifacts.append(artifact["id"])
        job = Job(workspace_id=workspace.id, adapter_id="notes", target="fixture://local/note")
        manager.save(job)
        expected_jobs.append(str(job.id))
    repository.add_artifact(other.id, "note", {"private": True})
    manager.save(Job(workspace_id=other.id, adapter_id="notes", target="fixture://other/note"))
    root = f"/api/workspaces/{workspace.id}"
    for resource, expected in [("artifacts", expected_artifacts), ("jobs", expected_jobs)]:
        actual = []
        for offset in range(0, 250, 50):
            response = client.get(root + f"/{resource}?limit=50&offset={offset}")
            assert response.status_code == 200
            actual.extend(item["id"] for item in response.json())
        assert actual == list(reversed(expected))
        assert client.get(root + f"/{resource}?offset=205").json() == []
    events = []
    for offset in range(0, 250, 50):
        events.extend(client.get(root + f"/audit?limit=50&offset={offset}").json())
    assert len(events) == 205
    assert len({event["id"] for event in events}) == 205
    assert all(event["metadata"]["artifact_id"] in expected_artifacts for event in events)
    workspaces = client.get("/api/workspaces?limit=200").json()
    workspaces += client.get("/api/workspaces?limit=200&offset=200").json()
    assert len({item["id"] for item in workspaces}) == 207
    assert UUID(workspaces[-1]["id"]) == workspace.id
    with pytest.raises(ValueError):
        repository.artifacts(workspace.id, limit=201)
    with pytest.raises(ValueError):
        manager.list(workspace.id, offset=-1)


@pytest.mark.parametrize("role", ["reader", "analyst", "admin"])
def test_scoped_credentials_permissions_and_revocation(client: TestClient, role: str) -> None:
    from uuid import UUID

    first, second = create_workspace(client), create_workspace(client)
    owner = client.headers["Authorization"]
    response = client.post(
        "/api/access-keys",
        json={
            "label": "QA account",
            "role": role,
            "workspace_ids": [] if role == "admin" else [first],
        },
    )
    assert response.status_code == 201
    key = response.json()
    assert key["token"] not in client.get("/api/access-keys").text
    with client.app.state.repository.store.connection() as db:
        row = db.execute("SELECT token_hash FROM access_keys WHERE id=?", (key["id"],)).fetchone()
        assert row[0] != key["token"]
        assert len(row[0]) == 64
    client.headers["Authorization"] = "Bearer " + key["token"]
    assert client.get("/api/session").json()["role"] == role
    visible = client.get("/api/workspaces?limit=1&offset=0").json()
    assert visible[0]["id"] == (second if role == "admin" else first)
    assert client.get(f"/api/workspaces/{first}").status_code == 200
    root = f"/api/workspaces/{second}"
    for suffix in ["", "/runs", "/scope", "/artifacts", "/audit", "/data-policy"]:
        assert client.get(root + suffix).status_code == (200 if role == "admin" else 403)
    assert client.post(root + "/export", json={}).status_code == (200 if role == "admin" else 403)
    assert client.get("/api/access-keys").status_code == (200 if role == "admin" else 403)
    assert client.post("/api/workspaces", json={"name": "Unauthorized"}).status_code == (
        201 if role == "admin" else 403
    )
    response = client.post(
        f"/api/workspaces/{first}/jobs",
        json={
            "adapter_id": "notes",
            "target": "fixture://local/sample",
            "payload": {"title": "Review", "note": "Local observation"},
        },
    )
    assert response.status_code == (403 if role == "reader" else 202)
    if role != "reader":
        wait_job(client, first, response.json()["id"])
    assert client.put(f"/api/workspaces/{first}/data-policy", json={}).status_code == (
        200 if role == "admin" else 403
    )
    # Cookie sessions retain role and are invalidated by revocation too.
    client.headers.pop("Authorization")
    assert client.post("/api/session", json={"token": key["token"]}).status_code == 200
    assert client.get("/api/session").json()["role"] == role
    client.headers["Authorization"] = owner
    assert client.delete("/api/access-keys/" + key["id"]).status_code == 200
    client.headers.pop("Authorization")
    assert client.get("/api/session").status_code == 401
    client.headers["Authorization"] = "Bearer " + key["token"]
    assert client.get(f"/api/workspaces/{first}/runs").status_code == 401
    assert UUID(first)


@pytest.mark.parametrize(
    "body",
    [
        {"label": "bad", "role": "reader", "workspace_ids": []},
        {"label": "bad", "role": "root", "workspace_ids": []},
    ],
)
def test_invalid_credentials(client: TestClient, body: dict) -> None:
    assert client.post("/api/access-keys", json=body).status_code == 422


def test_retention_protects_baselines_and_rejects_stale_preview(client: TestClient) -> None:
    from uuid import UUID

    workspace = create_workspace(client)
    root = f"/api/workspaces/{workspace}"
    payload = {
        "target": "fixture://local/sample",
        "target_version": "v1",
        "policy_version": "p1",
        "identity_set": "a",
        "checks": [{"id": "check", "title": "Check", "expected": "deny", "observed": "deny"}],
    }
    runs = []
    for _ in range(3):
        response = client.post(
            root + "/jobs",
            json={"adapter_id": "fixture", "target": payload["target"], "payload": payload},
        )
        runs.append(wait_job(client, workspace, response.json()["id"])["result"]["run_id"])
    store = client.app.state.repository.store
    for identity in runs:
        run = store.run(UUID(identity), UUID(workspace)).model_copy(
            update={"created_at": datetime.now(UTC) - timedelta(days=60)}
        )
        with store.connection() as db:
            db.execute("UPDATE runs SET payload=? WHERE id=?", (run.model_dump_json(), identity))
    assert (
        client.post(root + "/baselines", json={"name": "protected", "run_id": runs[0]}).status_code
        == 201
    )
    assert client.put(root + "/data-policy", json={"archive_after_days": 30}).status_code == 200
    preview = client.post(root + "/retention/preview", json={}).json()
    assert preview["count"] == 2
    assert runs[0] not in preview["run_ids"]
    # A baseline created after preview changes the selection and invalidates it.
    client.post(root + "/baselines", json={"name": "protected-later", "run_id": runs[1]})
    request = {key: preview[key] for key in ("as_of", "fingerprint")}
    assert client.post(root + "/retention/apply", json=request).status_code == 400
    assert client.get(root + "/runs?archived=true").json() == []
    preview = client.post(root + "/retention/preview", json={}).json()
    request = {key: preview[key] for key in ("as_of", "fingerprint")}
    assert client.post(root + "/retention/apply", json=request).json()["archived"] == 1
    assert client.post(root + "/retention/apply", json=request).status_code == 400
    assert client.get(root + "/runs?archived=true").json()[0]["id"] == runs[2]
    assert client.get(root + f"/runs/{runs[2]}/evidence").status_code == 200
    assert (
        client.put(root + f"/runs/{runs[2]}/archive", json={"archived": False}).status_code == 200
    )
    request["as_of"] = (datetime.now(UTC) - timedelta(minutes=11)).isoformat()
    assert client.post(root + "/retention/apply", json=request).status_code == 400
    request["as_of"] = datetime.now().isoformat()
    assert client.post(root + "/retention/apply", json=request).status_code == 400


def test_export_integrity_policy_and_workspace_isolation(client: TestClient, monkeypatch) -> None:
    from uuid import UUID

    from aegis.assurance import digest

    workspace, other = create_workspace(client), create_workspace(client)
    root = f"/api/workspaces/{workspace}"
    repository = client.app.state.repository
    artifact = repository.add_artifact(UUID(workspace), "note", {"note": "Included"})
    repository.add_artifact(UUID(other), "note", {"note": "Other workspace canary"})
    response = client.post(root + "/export", json={})
    assert response.status_code == 200
    bundle = response.json()
    assert bundle["manifest_sha256"] == digest(bundle["bundle"])
    assert "Other workspace canary" not in response.text
    assert "token_hash" not in response.text
    assert bundle["bundle"]["records"]["artifacts"][0]["id"] == artifact["id"]
    assert client.get(root + "/audit").json()[0]["action"] == "workspace.exported"
    client.put(root + "/data-policy", json={"include_evidence": False})
    assert "artifacts" not in client.post(root + "/export", json={}).json()["bundle"]["records"]
    client.put(root + "/data-policy", json={"export_allowed": False})
    assert client.post(root + "/export", json={}).status_code == 403
    client.put(root + "/data-policy", json={})
    monkeypatch.setattr("aegis.data_policy.MAX_EXPORT_BYTES", 10)
    assert client.post(root + "/export", json={}).status_code == 400
    monkeypatch.setattr("aegis.data_policy.MAX_EXPORT_BYTES", 10 * 1024 * 1024)
    with repository.store.connection() as db:
        db.execute(
            "UPDATE artifacts SET payload=? WHERE id=?", ('{"tampered":true}', artifact["id"])
        )
    assert client.post(root + "/export", json={}).status_code == 400


def test_sarif_job_persists_imported_observations(client: TestClient) -> None:
    workspace = create_workspace(client)
    root = f"/api/workspaces/{workspace}"
    response = client.post(
        root + "/jobs",
        json={
            "adapter_id": "sarif",
            "target": "fixture://local/sample",
            "payload": {
                "document": {
                    "version": "2.1.0",
                    "runs": [
                        {
                            "tool": {"driver": {"name": "Imported analyzer"}},
                            "results": [
                                {"ruleId": "rule-1", "message": {"text": "Review observation"}}
                            ],
                        }
                    ],
                }
            },
        },
    )
    assert response.status_code == 202
    job = wait_job(client, workspace, response.json()["id"])
    assert job["status"] == "succeeded"
    artifact = client.get(root + "/artifacts").json()[0]
    assert artifact["id"] == job["result"]["artifact_id"]
    assert artifact["data"]["adapter_id"] == "sarif"
    assert artifact["data"]["result_count"] == 1
    assert artifact["data"]["results"][0]["level"] == "warning"
    assert client.get(root + "/runs").json() == []


def test_threshold_run_keeps_measurements_and_bounds_in_evidence(client: TestClient) -> None:
    workspace = create_workspace(client, "qa")
    root = f"/api/workspaces/{workspace}"
    response = client.post(
        root + "/jobs",
        json={
            "adapter_id": "thresholds",
            "target": "fixture://local/sample",
            "payload": {
                "revision": "v1",
                "metrics": {"coverage": 0.5},
                "minimums": {"coverage": 0.8},
            },
        },
    )
    job = wait_job(client, workspace, response.json()["id"])
    evidence = client.get(root + "/runs/" + job["result"]["run_id"] + "/evidence").json()
    assert evidence["adapter_metadata"]["measurements"] == [{"name": "coverage", "value": 0.5}]
    assert evidence["adapter_metadata"]["bounds"] == [
        {"name": "coverage", "minimum": 0.8, "maximum": None}
    ]
    assert evidence["adapter_metadata"]["job_id"] == job["id"]
