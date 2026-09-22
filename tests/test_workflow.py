from uuid import UUID, uuid4

from test_api import create_workspace, wait_job


def observe(client, workspace, outcome, policy="p1"):
    root = f"/api/workspaces/{workspace}"
    response = client.post(
        root + "/jobs",
        json={
            "adapter_id": "fixture",
            "target": "fixture://local/sample",
            "payload": {
                "target": "fixture://local/sample",
                "target_version": "v1",
                "policy_version": policy,
                "identity_set": "alice-bob",
                "checks": [
                    {
                        "id": "ownership",
                        "title": "Ownership",
                        "expected": "deny",
                        "observed": outcome,
                        "severity": "high",
                    }
                ],
            },
        },
    )
    assert response.status_code == 202, response.text
    job = wait_job(client, workspace, response.json()["id"])
    assert job["status"] == "succeeded", job
    return client.get(root + "/runs").json()[0]["id"]


def test_remediation_retest_recurrence_and_edit_conflict(client):
    workspace = create_workspace(client)
    root = f"/api/workspaces/{workspace}"
    observe(client, workspace, "allow")
    first = client.get(root + "/workflow").json()["findings"][0]
    path = root + "/findings/" + first["id"]
    body = {
        "revision": 0,
        "status": "Triaged",
        "owner": "team",
        "notes": "token=secret-canary",
        "tags": ["regression"],
    }
    saved = client.put(path, json=body)
    assert saved.status_code == 200, saved.text
    assert "secret-canary" not in saved.text
    assert client.put(path, json=body).status_code == 400
    assert client.put(path, json={"revision": 1, "status": "Closed"}).status_code == 400
    observe(client, workspace, "deny", policy="different")
    assert client.get(root + "/workflow").json()["findings"][0]["retest"]["outcome"] == "FAIL"
    passing = observe(client, workspace, "deny")
    closed = client.put(path, json={"revision": 1, "status": "Closed", "closure_run_id": passing})
    assert closed.status_code == 200, closed.text
    observe(client, workspace, "allow")
    finding = client.get(root + "/workflow").json()["findings"][0]
    assert finding["closure_stale"] is True
    assert finding["trend"] == "recurring"
    assert len(finding["occurrences"]) == 2


def test_coverage_permissions_report_and_workspace_isolation(client):
    workspace = create_workspace(client)
    other = create_workspace(client)
    root = f"/api/workspaces/{workspace}"
    run = observe(client, workspace, "deny")
    path = root + "/coverage/" + str(uuid4())
    body = {
        "revision": 0,
        "framework": "Internal",
        "version": "1",
        "control": "AUTH-1",
        "title": "<script>alert(1)</script>",
        "status": "Passed",
        "rationale": "Reviewed",
    }
    assert client.put(path, json=body).status_code == 400
    body["evidence_run_id"] = observe(client, other, "deny")
    assert client.put(path, json=body).status_code == 400
    body["evidence_run_id"] = run
    assert client.put(path, json=body).status_code == 200
    report = client.get(root + "/stakeholder-report")
    assert report.status_code == 200
    assert "<script>" not in report.text and "&lt;script&gt;" in report.text
    assert client.get(f"/api/workspaces/{other}/workflow").json()["coverage"] == []
    key = client.post(
        "/api/access-keys", json={"label": "reader", "role": "reader", "workspace_ids": [workspace]}
    ).json()
    headers = {"Authorization": "Bearer " + key["token"]}
    assert client.put(path, json=body, headers=headers).status_code == 403
    assert client.get(f"/api/workspaces/{other}/workflow", headers=headers).status_code == 403
    assert client.get("/api/readiness", headers=headers).status_code == 200
    assert client.get(root + "/workflow", headers=headers).status_code == 200
    policies = client.app.state.repository.store
    from aegis.data_policy import DataPolicies, DataPolicy

    DataPolicies(policies).save(UUID(workspace), DataPolicy(export_allowed=False), "test")
    assert client.get(root + "/stakeholder-report").status_code == 403


def test_sarif_dedup_preserves_source_and_never_infers_fix(client):
    workspace = create_workspace(client)
    root = f"/api/workspaces/{workspace}"
    payload = {
        "document": {
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {"driver": {"name": "Example"}},
                    "results": [
                        {
                            "ruleId": "R1",
                            "message": {"text": "Example finding"},
                            "level": "warning",
                        },
                        {
                            "ruleId": "R2",
                            "message": {"text": "Suppressed"},
                            "suppressions": [{"kind": "external", "status": "accepted"}],
                        },
                    ],
                }
            ],
        }
    }
    for _ in range(2):
        job = client.post(
            root + "/jobs",
            json={"adapter_id": "sarif", "target": "fixture://local/sample", "payload": payload},
        ).json()
        assert wait_job(client, workspace, job["id"])["status"] == "succeeded"
    findings = client.get(root + "/workflow").json()["findings"]
    assert len(findings) == 1 and len(findings[0]["occurrences"]) == 2
    assert findings[0]["retest"]["outcome"] == "UNKNOWN"
    assert (
        client.put(
            root + "/findings/" + findings[0]["id"], json={"revision": 0, "status": "Closed"}
        ).status_code
        == 400
    )
    passing = observe(client, workspace, "deny")
    path = root + "/findings/" + findings[0]["id"]
    response = client.put(
        path,
        json={
            "revision": 0,
            "status": "Closed",
            "closure_run_id": passing,
            "closure_check_id": "ownership",
            "notes": "Analyst mapped this retest to R1",
        },
    )
    assert response.status_code == 200, response.text
    finding = client.get(root + "/workflow").json()["findings"][0]
    assert finding["retest"]["basis"] == "Analyst-linked recorded retest"
    assert finding["closure_stale"] is False
    observe(client, workspace, "allow")
    assert (
        next(
            f
            for f in client.get(root + "/workflow").json()["findings"]
            if f["id"] == findings[0]["id"]
        )["closure_stale"]
        is True
    )
