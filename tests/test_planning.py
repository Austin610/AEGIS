from uuid import uuid4

from test_api import create_workspace


def test_scrum_planning_subtasks_done_and_burndown(client):
    workspace = create_workspace(client)
    root = f"/api/workspaces/{workspace}"
    sprint_id, story_id, child_id = (str(uuid4()) for _ in range(3))
    sprint = {
        "name": "Sprint 1",
        "goal": "Complete a workflow",
        "starts_on": "2026-09-14",
        "duration_days": 10,
        "capacity_points": 8,
    }
    assert client.put(root + "/sprints/" + sprint_id, json=sprint).status_code == 200
    story = {"title": "As an analyst I can triage", "story_points": 5, "sprint_id": sprint_id}
    assert client.put(root + "/items/" + story_id, json=story).status_code == 200
    child = {"title": "Implement form", "kind": "subtask", "parent_id": story_id}
    assert client.put(root + "/items/" + child_id, json=child).status_code == 200
    sprint.update(revision=1, status="Active")
    assert client.put(root + "/sprints/" + sprint_id, json=sprint).status_code == 200
    snapshot = client.get(root + "/planning").json()
    assert snapshot["sprints"][0]["committed_points"] == 5
    assert snapshot["sprints"][0]["remaining_points"] == 5
    assert snapshot["burndown"][0]["remaining_points"] == 5
    story.update(revision=1, status="Done")
    assert client.put(root + "/items/" + story_id, json=story).status_code == 422
    checks = {
        "acceptance_criteria": [{"text": "Reviewed", "done": True}],
        "definition_of_done": [{"text": "Tests passed", "done": True}],
    }
    story.update(checks)
    assert client.put(root + "/items/" + story_id, json=story).status_code == 400
    child.update(revision=1, status="Done", **checks)
    assert client.put(root + "/items/" + child_id, json=child).status_code == 200
    assert client.put(root + "/items/" + story_id, json=story).status_code == 200
    assert client.put(root + "/items/" + story_id, json=story).status_code == 400
    snapshot = client.get(root + "/planning").json()
    assert snapshot["sprints"][0]["remaining_points"] == 0
    assert snapshot["burndown"][0]["remaining_points"] == 0
    sprint.update(revision=2, status="Completed", review="Delivered", retrospective="Smaller tasks")
    assert client.put(root + "/sprints/" + sprint_id, json=sprint).status_code == 200
    snapshot = client.get(root + "/planning").json()
    assert snapshot["sprints"][0]["review"] == "Delivered"
    assert snapshot["sprints"][0]["ends_on"] == "2026-09-24"


def test_planning_isolation_cycles_and_permissions(client):
    workspace, other = create_workspace(client), create_workspace(client)
    root = f"/api/workspaces/{workspace}"
    parent, child = str(uuid4()), str(uuid4())
    assert client.put(root + "/items/" + parent, json={"title": "Parent"}).status_code == 200
    assert (
        client.put(
            root + "/items/" + child, json={"title": "Child", "parent_id": parent}
        ).status_code
        == 200
    )
    assert (
        client.put(
            root + "/items/" + parent, json={"title": "Cycle", "parent_id": child, "revision": 1}
        ).status_code
        == 400
    )
    assert (
        client.put(
            f"/api/workspaces/{other}/items/{uuid4()}",
            json={"title": "Cross workspace", "parent_id": parent},
        ).status_code
        == 400
    )
    key = client.post(
        "/api/access-keys", json={"label": "read", "role": "reader", "workspace_ids": [workspace]}
    ).json()["token"]
    headers = {"Authorization": "Bearer " + key}
    assert client.get(root + "/planning", headers=headers).status_code == 200
    assert (
        client.put(root + "/items/" + parent, json={"title": "No"}, headers=headers).status_code
        == 403
    )
    assert client.get(f"/api/workspaces/{other}/planning", headers=headers).status_code == 403


def test_active_sprint_contract_and_no_double_counted_task_points(client):
    workspace = create_workspace(client)
    root = f"/api/workspaces/{workspace}"
    first, second = str(uuid4()), str(uuid4())
    body = {"name": "Sprint", "goal": "Work", "starts_on": "2026-09-14", "status": "Active"}
    assert client.put(root + "/sprints/" + first, json=body).status_code == 200
    assert client.put(root + "/sprints/" + second, json=body).status_code == 400
    body.update(revision=1, duration_days=30)
    assert client.put(root + "/sprints/" + first, json=body).status_code == 400
    assert (
        client.put(
            root + "/items/" + str(uuid4()),
            json={"title": "Task", "kind": "task", "story_points": 5},
        ).status_code
        == 422
    )
