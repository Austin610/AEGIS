import pytest
from test_api import create_workspace, wait_job


@pytest.mark.parametrize(
    "mode,adapter,payload",
    [
        (
            "appsec",
            "invariants",
            {
                "revision": "1",
                "observations": {"status": 403},
                "checks": [
                    {
                        "id": "deny",
                        "title": "Recorded denial",
                        "path": ["status"],
                        "operator": "equals",
                        "expected": 403,
                    }
                ],
            },
        ),
        (
            "qa",
            "junit",
            {
                "revision": "1",
                "suite_version": "1",
                "xml": '<testsuite name="qa"><testcase name="passes"/></testsuite>',
            },
        ),
        (
            "api_security",
            "openapi",
            {
                "document": {
                    "openapi": "3.0.3",
                    "info": {"title": "Sample", "version": "1"},
                    "paths": {"/items": {"get": {"responses": {"200": {"description": "OK"}}}}},
                }
            },
        ),
        (
            "research",
            "evaluation",
            {
                "name": "Labeled observations",
                "samples": [
                    {"truth": True, "predicted": True},
                    {"truth": False, "predicted": None},
                ],
            },
        ),
        ("forensics", "file-metadata", {"name": "evidence.txt", "content_base64": "ZXZpZGVuY2U="}),
        ("reverse", "file-metadata", {"name": "sample.bin", "content_base64": "TVpzb21lIGJ5dGVz"}),
        (
            "ctf",
            "notes",
            {
                "title": "Challenge notebook",
                "note": "Supplied challenge observations; no execution",
            },
        ),
        (
            "bug_bounty",
            "notes",
            {"title": "Disclosure draft", "note": "Manual evidence summary for review"},
        ),
        (
            "training",
            "invariants",
            {
                "revision": "synthetic-1",
                "observations": {"flag": False},
                "checks": [
                    {
                        "id": "synthetic",
                        "title": "Synthetic exercise",
                        "path": ["flag"],
                        "operator": "equals",
                        "expected": True,
                    }
                ],
            },
        ),
    ],
)
def test_mode_acceptance_persists_offline_result(client, mode, adapter, payload):
    workspace = create_workspace(client, mode)
    root = f"/api/workspaces/{workspace}"
    response = client.post(
        root + "/jobs",
        json={"adapter_id": adapter, "target": "fixture://local/sample", "payload": payload},
    )
    assert response.status_code == 202, response.text
    job = wait_job(client, workspace, response.json()["id"])
    assert job["status"] == "succeeded", job
    if job["result"].get("run_id"):
        evidence = client.get(root + "/runs/" + job["result"]["run_id"] + "/evidence").json()
        assert evidence["adapter_metadata"]["adapter_id"] == adapter
    else:
        artifact = client.get(root + "/artifacts").json()[0]
        assert artifact["data"]["adapter_id"] == adapter and len(artifact["sha256"]) == 64
    assert client.get(root + "/audit").json()[0]["action"] == "job.succeeded"
