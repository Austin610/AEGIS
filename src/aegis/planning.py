"""Workspace-owned work items and Scrum planning, independent of execution adapters."""

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from aegis.policy import Model
from aegis.repository import Repository, current_actor
from aegis.service import sanitize_metadata

BoardStatus = Literal["To Do", "In Progress", "Review", "Testing", "Done"]
COLUMNS = ["To Do", "In Progress", "Review", "Testing", "Done"]


class Criterion(Model):
    text: str = Field(min_length=1, max_length=500)
    done: bool = False


class WorkItem(Model):
    revision: int = Field(default=0, ge=0)
    title: str = Field(min_length=1, max_length=300)
    kind: Literal["story", "task", "subtask", "bug"] = "story"
    description: str = Field(default="", max_length=20000)
    status: BoardStatus = "To Do"
    priority: Literal["P0", "P1", "P2", "P3"] = "P2"
    assignee: str = Field(default="", max_length=200)
    story_points: int = Field(default=0, ge=0, le=100)
    rank: int = Field(default=0, ge=0, le=1000000)
    parent_id: UUID | None = None
    sprint_id: UUID | None = None
    finding_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    acceptance_criteria: list[Criterion] = Field(default_factory=list, max_length=50)
    definition_of_done: list[Criterion] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_work(self) -> "WorkItem":
        if self.kind == "subtask" and self.parent_id is None:
            raise ValueError("Subtasks require a parent")
        if self.parent_id and self.sprint_id:
            raise ValueError("Child items inherit their parent's sprint")
        if self.kind in {"task", "subtask"} and self.story_points:
            raise ValueError("Estimate stories and bugs; tasks do not add story points")
        if self.parent_id and self.story_points:
            raise ValueError("Child items do not carry separate story point estimates")
        if self.status == "Done" and (
            not self.acceptance_criteria
            or not self.definition_of_done
            or not all(c.done for c in self.acceptance_criteria + self.definition_of_done)
        ):
            raise ValueError("Done requires completed acceptance criteria and Definition of Done")
        return self


class Sprint(Model):
    revision: int = Field(default=0, ge=0)
    name: str = Field(min_length=1, max_length=200)
    goal: str = Field(default="", max_length=5000)
    starts_on: date
    duration_days: int = Field(default=14, ge=1, le=90)
    capacity_points: int = Field(default=0, ge=0, le=10000)
    status: Literal["Planned", "Active", "Completed"] = "Planned"
    review: str = Field(default="", max_length=20000)
    retrospective: str = Field(default="", max_length=20000)


class Planning:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def _records(self, db: Any, workspace: UUID) -> dict[str, Any]:
        records = db.execute(
            "SELECT kind,identity,payload FROM workflow_records "
            "WHERE workspace_id=? AND kind IN ('item','sprint','burndown') "
            "ORDER BY rowid",
            (str(workspace),),
        ).fetchmany(10001)
        if len(records) > 10000 or sum(len(r[2]) for r in records) > 10 * 1024 * 1024:
            raise ValueError("Planning snapshot exceeds capacity")
        result: dict[str, Any] = {"items": [], "sprints": [], "burndown": [], "columns": COLUMNS}
        for kind, identity, payload in records:
            result[{"item": "items", "sprint": "sprints", "burndown": "burndown"}[kind]].append(
                {**json.loads(payload), "id": identity}
            )
        result["items"].sort(key=lambda item: (item["rank"], item["id"]))
        return result

    @staticmethod
    def effective_sprint(item: dict[str, Any], items: dict[str, Any]) -> str | None:
        seen = set()
        while item.get("parent_id"):
            if item["id"] in seen:
                raise ValueError("Parent cycle")
            seen.add(item["id"])
            item = items[item["parent_id"]]
        sprint: str | None = item.get("sprint_id")
        return sprint

    def snapshot(self, workspace: UUID) -> dict[str, Any]:
        self.repository.store.workspace(workspace)
        with self.repository.store.connection() as db:
            db.execute("BEGIN")
            result = self._records(db, workspace)
        items = {i["id"]: i for i in result["items"]}
        for item in result["items"]:
            item["effective_sprint_id"] = self.effective_sprint(item, items)
        for sprint in result["sprints"]:
            scope = [i for i in result["items"] if i["effective_sprint_id"] == sprint["id"]]
            sprint["total_points"] = sum(i["story_points"] for i in scope if not i["parent_id"])
            sprint["remaining_points"] = sum(
                i["story_points"] for i in scope if not i["parent_id"] and i["status"] != "Done"
            )
            sprint["completed_items"] = sum(i["status"] == "Done" for i in scope)
            sprint["total_items"] = len(scope)
            sprint["ends_on"] = (
                date.fromisoformat(sprint["starts_on"]) + timedelta(days=sprint["duration_days"])
            ).isoformat()
            if sprint["status"] == "Completed" and sprint.get("completion_snapshot"):
                sprint.update(sprint["completion_snapshot"])
        return result

    def save(
        self,
        workspace: UUID,
        kind: Literal["item", "sprint"],
        identity: UUID,
        value: WorkItem | Sprint,
    ) -> dict[str, Any]:
        self.repository.store.workspace(workspace)
        payload = value.model_dump(mode="json")
        # Sanitize free text before storage; identifiers and dates remain canonical.
        for key in (
            "title",
            "description",
            "assignee",
            "name",
            "goal",
            "review",
            "retrospective",
            "acceptance_criteria",
            "definition_of_done",
        ):
            if key in payload:
                payload[key] = sanitize_metadata(payload[key])
        now = datetime.now(UTC)
        with self.repository.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            snapshot = self._records(db, workspace)
            items = {i["id"]: i for i in snapshot["items"]}
            sprints = {s["id"]: s for s in snapshot["sprints"]}
            old = (items if kind == "item" else sprints).get(str(identity))
            if value.revision != (old["revision"] if old else 0):
                raise ValueError("Record changed; reload before saving")
            if isinstance(value, WorkItem):
                if value.parent_id:
                    parent = items.get(str(value.parent_id))
                    if parent is None or str(value.parent_id) == str(identity):
                        raise ValueError("Parent must be another item in this workspace")
                    if parent["status"] == "Done" and value.status != "Done":
                        raise ValueError("Reopen the parent before adding unfinished work")
                if value.sprint_id and str(value.sprint_id) not in sprints:
                    raise ValueError("Sprint must belong to this workspace")
                if value.finding_id:
                    from aegis.workflow import Workflow

                    if value.finding_id not in {
                        f["id"]
                        for f in Workflow(self.repository).snapshot(workspace, db)["findings"]
                    }:
                        raise ValueError("Finding must belong to this workspace")
                items[str(identity)] = {**payload, "id": str(identity)}
                destination = self.effective_sprint(items[str(identity)], items)
                if destination and sprints[destination]["status"] == "Completed":
                    raise ValueError(
                        "Move work to an open sprint or product backlog before editing"
                    )
                if value.status == "Done" and any(
                    i["parent_id"] == str(identity) and i["status"] != "Done"
                    for i in items.values()
                ):
                    raise ValueError("Complete child work before marking the parent Done")
                for item in items.values():
                    self.effective_sprint(item, items)
            else:
                if old and old["status"] == "Completed" and value.status != "Completed":
                    raise ValueError("Completed sprints cannot be reopened")
                if value.status == "Active":
                    if not value.goal.strip():
                        raise ValueError("Sprint activation requires a goal")
                    if any(
                        s["status"] == "Active" and s["id"] != str(identity)
                        for s in sprints.values()
                    ):
                        raise ValueError("Only one active sprint per workspace")
                if (
                    old
                    and old["status"] in {"Active", "Completed"}
                    and (
                        value.starts_on.isoformat() != old["starts_on"]
                        or value.duration_days != old["duration_days"]
                        or value.status == "Planned"
                    )
                ):
                    raise ValueError("Active sprint dates and duration are fixed")
                if value.status == "Completed" and (not old or old["status"] == "Planned"):
                    raise ValueError("Activate the sprint before completing it")
                payload["committed_points"] = (old or {}).get("committed_points")
                payload["completion_snapshot"] = (old or {}).get("completion_snapshot")
                if value.status == "Completed" and old and old["status"] == "Active":
                    scope = [
                        i
                        for i in items.values()
                        if self.effective_sprint(i, items) == str(identity)
                    ]
                    payload["completion_snapshot"] = {
                        "total_points": sum(i["story_points"] for i in scope if not i["parent_id"]),
                        "remaining_points": sum(
                            i["story_points"]
                            for i in scope
                            if not i["parent_id"] and i["status"] != "Done"
                        ),
                        "total_items": len(scope),
                        "completed_items": sum(i["status"] == "Done" for i in scope),
                        "item_ids": [i["id"] for i in scope],
                    }
                if value.status == "Active" and (not old or old["status"] == "Planned"):
                    payload["committed_points"] = sum(
                        i["story_points"]
                        for i in items.values()
                        if not i["parent_id"] and i["sprint_id"] == str(identity)
                    )
                sprints[str(identity)] = {**payload, "id": str(identity)}
            payload["revision"] = value.revision + 1
            payload["updated_at"] = now.isoformat()
            self._write(db, workspace, kind, str(identity), payload)
            for sprint_id, sprint in sprints.items():
                if sprint["status"] != "Active" and not (
                    kind == "sprint"
                    and sprint_id == str(identity)
                    and sprint["status"] == "Completed"
                ):
                    continue
                scope = [
                    i
                    for i in items.values()
                    if not i["parent_id"] and self.effective_sprint(i, items) == sprint_id
                ]
                self._write(
                    db,
                    workspace,
                    "burndown",
                    sprint_id + ":" + now.date().isoformat(),
                    {
                        "sprint_id": sprint_id,
                        "date": now.date().isoformat(),
                        "scope_points": sum(i["story_points"] for i in scope),
                        "remaining_points": sum(
                            i["story_points"] for i in scope if i["status"] != "Done"
                        ),
                    },
                )
            db.execute(
                "INSERT INTO audit_events(id,workspace_id,created_at,action,payload) "
                "VALUES (?,?,?,?,?)",
                (
                    str(uuid4()),
                    str(workspace),
                    now.isoformat(),
                    "planning." + kind,
                    json.dumps(
                        {"actor": current_actor.get(), "id": str(identity), "record": payload}
                    ),
                ),
            )
        return {**payload, "id": str(identity)}

    @staticmethod
    def _write(db: Any, workspace: UUID, kind: str, identity: str, payload: dict[str, Any]) -> None:
        db.execute(
            "INSERT INTO workflow_records(workspace_id,kind,identity,payload) VALUES (?,?,?,?) "
            "ON CONFLICT(workspace_id,kind,identity) DO UPDATE SET payload=excluded.payload",
            (str(workspace), kind, identity, json.dumps(payload)),
        )
