"""Bounded local job queue with cooperative cancellation and persisted state."""

import asyncio
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from aegis.adapter_contract import Registry
from aegis.policy import Model, authorize
from aegis.repository import Repository, validate_page
from aegis.service import import_fixture


class Job(Model):
    id: UUID = Field(default_factory=uuid4)
    workspace_id: UUID
    adapter_id: str
    target: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"] = "queued"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    result: dict[str, object] = Field(default_factory=dict)
    error: str | None = None


class JobManager:
    def __init__(self, repository: Repository, registry: Registry) -> None:
        self.repository, self.registry = repository, registry
        self.tasks: dict[UUID, asyncio.Task[None]] = {}
        self.semaphore = asyncio.Semaphore(2)

    def save(self, job: Job) -> None:
        with self.repository.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT mode FROM job_runtime WHERE id=1").fetchone()[0] != "legacy":
                raise ValueError("Database uses durable jobs; restart with durable mode enabled")
            db.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "status=excluded.status, payload=excluded.payload",
                (
                    str(job.id),
                    str(job.workspace_id),
                    job.status,
                    job.model_dump_json(),
                ),
            )

    def get(self, workspace_id: UUID, identity: UUID) -> Job:
        with self.repository.store.connection() as db:
            row = db.execute(
                "SELECT payload FROM jobs WHERE id=? AND workspace_id=?",
                (str(identity), str(workspace_id)),
            ).fetchone()
        if row is None:
            raise ValueError("Job not found in workspace")
        return Job.model_validate_json(row[0])

    def list(self, workspace_id: UUID, *, limit: int = 100, offset: int = 0) -> list[Job]:
        validate_page(limit, offset)
        self.repository.store.workspace(workspace_id)
        with self.repository.store.connection() as db:
            rows = db.execute(
                "SELECT payload FROM jobs WHERE workspace_id=? "
                "ORDER BY rowid DESC LIMIT ? OFFSET ?",
                (str(workspace_id), limit, offset),
            ).fetchall()
        return [Job.model_validate_json(row[0]) for row in rows]

    def recover(self) -> None:
        with self.repository.store.connection() as db:
            if db.execute("SELECT mode FROM job_runtime WHERE id=1").fetchone()[0] != "legacy":
                raise ValueError("Database uses durable jobs; restart with durable mode enabled")
            rows = db.execute(
                "SELECT payload FROM jobs WHERE status IN ('queued','running')"
            ).fetchall()
        for row in rows:
            job = Job.model_validate_json(row[0]).model_copy(
                update={
                    "status": "failed",
                    "error": "interrupted_by_restart",
                    "updated_at": datetime.now(UTC),
                }
            )
            self.save(job)
            self.repository.audit(job.workspace_id, "job.interrupted", {"job_id": str(job.id)})

    async def start(self) -> None:
        self.recover()

    async def submit(
        self, workspace_id: UUID, adapter_id: str, target: str, payload: dict[str, object]
    ) -> Job:
        if len(self.tasks) >= 32:
            raise ValueError("Job queue full")
        adapter = self.registry.get(adapter_id)
        workspace = self.repository.store.workspace(workspace_id)
        if workspace.mode not in adapter.modes:
            self.repository.audit(workspace_id, "job.mode_denied", {"adapter_id": adapter_id})
            raise ValueError("Adapter not allowed in this mode")
        decision = authorize(
            workspace, self.repository.scope(workspace_id), target, adapter.capability
        )
        self.repository.store.record_decision(decision)
        if not decision.allowed:
            self.repository.audit(workspace_id, "job.scope_denied", {"reason": decision.reason})
            raise ValueError("Scope denied")
        job = Job(workspace_id=workspace_id, adapter_id=adapter_id, target=target)
        self.save(job)
        self.repository.audit(
            workspace_id, "job.queued", {"job_id": str(job.id), "adapter_id": adapter_id}
        )
        task = asyncio.create_task(self.execute(job, payload))
        self.tasks[job.id] = task
        task.add_done_callback(lambda _: self.tasks.pop(job.id, None))
        return job

    async def execute(self, job: Job, payload: dict[str, object]) -> None:
        try:
            async with self.semaphore:
                job = job.model_copy(update={"status": "running", "updated_at": datetime.now(UTC)})
                self.save(job)
                adapter = self.registry.get(job.adapter_id)
                if not await adapter.healthcheck():
                    raise ValueError("Adapter unavailable")
                workspace = self.repository.store.workspace(job.workspace_id)
                scope = self.repository.scope(job.workspace_id)
                decision = authorize(workspace, scope, job.target, adapter.capability)
                self.repository.store.record_decision(decision)
                if not decision.allowed:
                    raise ValueError("Scope no longer allows execution")
                output = await asyncio.wait_for(adapter.execute(job.target, payload), timeout=10)
                # Cancellation can prevent persistence after parsing completes.
                await asyncio.sleep(0)
                scope = self.repository.scope(job.workspace_id)
                decision = authorize(workspace, scope, job.target, adapter.capability)
                self.repository.store.record_decision(decision)
                if not decision.allowed:
                    raise ValueError("Scope no longer allows result persistence")
                if output.fixture is not None:
                    if scope is None:
                        raise ValueError("Scope missing")
                    run = import_fixture(
                        self.repository.store,
                        job.workspace_id,
                        scope,
                        output.fixture,
                        source_metadata={
                            "adapter_id": adapter.id,
                            "adapter_version": adapter.version,
                            "job_id": str(job.id),
                            **output.data,
                        },
                        source_kind="junit_observation"
                        if adapter.id == "junit"
                        else "fixture_observation",
                    )
                    result: dict[str, object] = {
                        "run_id": str(run.id),
                        "findings": len(run.findings),
                    }
                else:
                    artifact = self.repository.add_artifact(
                        job.workspace_id,
                        output.kind,
                        {
                            **output.data,
                            "adapter_id": adapter.id,
                            "adapter_version": adapter.version,
                            "scope_decision_id": str(decision.id),
                            "job_id": str(job.id),
                        },
                    )
                    result = {"artifact_id": artifact["id"]}
                job = job.model_copy(update={"status": "succeeded", "result": result})
        except asyncio.CancelledError:
            job = job.model_copy(update={"status": "cancelled", "error": "cancelled_by_operator"})
        except TimeoutError:
            job = job.model_copy(update={"status": "failed", "error": "adapter_timeout"})
        except Exception:
            # Worker boundary: record failure without persisting untrusted exception text.
            job = job.model_copy(update={"status": "failed", "error": "adapter_or_storage_error"})
        finally:
            job = job.model_copy(update={"updated_at": datetime.now(UTC)})
            self.save(job)
            self.repository.audit(job.workspace_id, "job." + job.status, {"job_id": str(job.id)})

    async def cancel(self, workspace_id: UUID, identity: UUID) -> Job:
        job = self.get(workspace_id, identity)
        task = self.tasks.get(identity)
        if task and job.status in ("queued", "running"):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # A task cancelled before its first instruction cannot run its finally block.
            if self.get(workspace_id, identity).status in ("queued", "running"):
                self.save(
                    job.model_copy(update={"status": "cancelled", "updated_at": datetime.now(UTC)})
                )
                self.repository.audit(workspace_id, "job.cancelled", {"job_id": str(identity)})
        return self.get(workspace_id, identity)

    async def shutdown(self) -> None:
        identities = list(self.tasks)
        for identity in identities:
            task = self.tasks.get(identity)
            if task:
                task.cancel()
        await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
        # Tasks cancelled before entering execute() cannot run its finally block.
        with self.repository.store.connection() as db:
            rows = db.execute(
                "SELECT payload FROM jobs WHERE status IN ('queued','running')"
            ).fetchall()
        for row in rows:
            job = Job.model_validate_json(row[0]).model_copy(
                update={
                    "status": "cancelled",
                    "error": "server_shutdown",
                    "updated_at": datetime.now(UTC),
                }
            )
            self.save(job)
            self.repository.audit(job.workspace_id, "job.cancelled", {"job_id": str(job.id)})
