"""Leased, fenced execution for bounded offline adapters. No external side effects are retried."""

import asyncio
import hashlib
import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any
from uuid import UUID, uuid4

from aegis.adapter_contract import AdapterOutput, Registry
from aegis.jobs import Job, JobManager
from aegis.policy import authorize
from aegis.repository import Repository, current_actor
from aegis.service import import_fixture, sanitize_metadata
from aegis.store import TransactionStore


def database_now(db: Any) -> datetime:
    query = (
        "SELECT CURRENT_TIMESTAMP"
        if isinstance(db, sqlite3.Connection)
        else "SELECT clock_timestamp()"
    )
    value = datetime.fromisoformat(str(db.execute(query).fetchone()[0]))
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class DurableJobManager(JobManager):
    lease_seconds = 30
    max_attempts = 3

    def __init__(self, repository: Repository, registry: Registry, *, worker: bool = True) -> None:
        super().__init__(repository, registry)
        self.worker = worker
        self.worker_id = str(uuid4())
        self.stop = asyncio.Event()
        self.dispatcher: asyncio.Task[None] | None = None
        with repository.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM jobs j LEFT JOIN job_queue q ON q.job_id=j.id "
                "WHERE j.status IN ('queued','running') AND q.job_id IS NULL LIMIT 1"
            ).fetchone():
                raise ValueError("Recover and drain legacy jobs before enabling durable execution")
            db.execute("UPDATE job_runtime SET mode='durable' WHERE id=1")

    @staticmethod
    def write_job(db: Any, job: Job) -> None:
        db.execute(
            "INSERT INTO jobs(id,workspace_id,status,payload) VALUES (?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status,payload=excluded.payload",
            (str(job.id), str(job.workspace_id), job.status, job.model_dump_json()),
        )

    @staticmethod
    def audit(db: Any, job: Job, actor: str, **metadata: object) -> None:
        Repository(TransactionStore(db)).audit(
            job.workspace_id,
            "job." + job.status,
            {"actor": actor, "job_id": str(job.id), "queue": "durable", **metadata},
        )

    async def submit(
        self, workspace_id: UUID, adapter_id: str, target: str, payload: dict[str, object]
    ) -> Job:
        # Do not silently change checks or source hashes to make a payload persistable.
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(raw.encode()) > 1_048_576:
            raise ValueError("Durable input exceeds 1 MiB")
        if sanitize_metadata(payload) != payload:
            raise ValueError("Redact common secret patterns before submitting durable input")
        adapter = self.registry.get(adapter_id)
        actor = current_actor.get()
        with self.repository.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            repository = Repository(TransactionStore(db))
            workspace = repository.store.workspace(workspace_id)
            if workspace.mode not in adapter.modes:
                raise ValueError("Adapter not allowed in this mode")
            if (
                db.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')"
                ).fetchone()[0]
                >= 32
            ):
                raise ValueError("Job queue full")
            decision = authorize(
                workspace, repository.scope(workspace_id), target, adapter.capability
            )
            repository.store.record_decision(decision)
            if decision.allowed:
                job = Job(workspace_id=workspace_id, adapter_id=adapter_id, target=target)
                self.write_job(db, job)
                db.execute(
                    "INSERT INTO job_queue(job_id,input,input_sha256,adapter_version,actor) "
                    "VALUES (?,?,?,?,?)",
                    (
                        str(job.id),
                        raw,
                        hashlib.sha256(raw.encode()).hexdigest(),
                        adapter.version,
                        actor,
                    ),
                )
                self.audit(db, job, actor, adapter_id=adapter_id)
            else:
                repository.audit(
                    workspace_id, "job.scope_denied", {"actor": actor, "reason": decision.reason}
                )
        if not decision.allowed:
            raise ValueError("Scope denied")
        return job

    def recover(self) -> None:
        """Only expired claims are recovered; queued work and live workers are untouched."""
        with self.repository.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            now = database_now(db)
            rows = db.execute(
                "SELECT j.payload,q.attempts,q.actor FROM jobs j JOIN job_queue q "
                "ON q.job_id=j.id WHERE j.status='running' AND q.lease_until<=?",
                (now.isoformat(),),
            ).fetchall()
            for raw, attempts, actor in rows:
                job = Job.model_validate_json(raw).model_copy(
                    update={
                        "status": "queued" if attempts < self.max_attempts else "failed",
                        "error": None if attempts < self.max_attempts else "worker_retry_exhausted",
                        "updated_at": now,
                    }
                )
                self.write_job(db, job)
                if job.status == "queued":
                    db.execute(
                        "UPDATE job_queue SET owner=NULL,lease_until=NULL WHERE job_id=?",
                        (str(job.id),),
                    )
                else:
                    db.execute("DELETE FROM job_queue WHERE job_id=?", (str(job.id),))
                self.audit(db, job, actor, reason="expired_worker_lease", attempts=attempts)

    def claim(self) -> tuple[Job, dict[str, object], str, str] | None:
        with self.repository.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            now = database_now(db)
            exclude = (
                (" AND j.id NOT IN (" + ",".join("?" for _ in self.tasks) + ")")
                if self.tasks
                else ""
            )
            row = db.execute(
                "SELECT j.payload,q.input,q.input_sha256,q.adapter_version,q.actor "
                "FROM jobs j JOIN job_queue q ON q.job_id=j.id "
                "WHERE j.status='queued'" + exclude + " ORDER BY j.rowid LIMIT 1",
                tuple(str(identity) for identity in self.tasks),
            ).fetchone()
            if row is None:
                return None
            job = Job.model_validate_json(row[0])
            repository = Repository(TransactionStore(db))
            try:
                adapter = self.registry.get(job.adapter_id)
                if (
                    adapter.version != row[3]
                    or hashlib.sha256(row[1].encode()).hexdigest() != row[2]
                ):
                    raise ValueError("Queued input or adapter version mismatch")
                workspace = repository.store.workspace(job.workspace_id)
                decision = authorize(
                    workspace, repository.scope(job.workspace_id), job.target, adapter.capability
                )
                repository.store.record_decision(decision)
                if not decision.allowed or workspace.mode not in adapter.modes:
                    raise ValueError("Scope no longer allows execution")
                payload: dict[str, object] = json.loads(row[1])
            except (ValueError, TypeError):
                failed = job.model_copy(
                    update={
                        "status": "failed",
                        "error": "queue_validation_failed",
                        "updated_at": now,
                    }
                )
                self.write_job(db, failed)
                db.execute("DELETE FROM job_queue WHERE job_id=?", (str(job.id),))
                self.audit(db, failed, row[4])
                return None
            token = self.worker_id + ":" + str(uuid4())
            job = job.model_copy(update={"status": "running", "updated_at": now})
            self.write_job(db, job)
            db.execute(
                "UPDATE job_queue SET owner=?,lease_until=?,attempts=attempts+1 WHERE job_id=?",
                (token, (now + timedelta(seconds=self.lease_seconds)).isoformat(), str(job.id)),
            )
            self.audit(db, job, row[4], worker_id=self.worker_id)
            return job, payload, token, row[4]

    def owns(self, db: Any, job: Job, token: str) -> bool:
        row = db.execute(
            "SELECT q.owner,q.lease_until,j.status FROM job_queue q JOIN jobs j "
            "ON j.id=q.job_id WHERE q.job_id=?",
            (str(job.id),),
        ).fetchone()
        return bool(
            row
            and row[0] == token
            and row[2] == "running"
            and datetime.fromisoformat(row[1]) > database_now(db)
        )

    def persist(self, job: Job, output: AdapterOutput, token: str, actor: str) -> None:
        with self.repository.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if not self.owns(db, job, token):
                return  # Another worker, cancellation or expiry fenced this attempt out.
            repository = Repository(TransactionStore(db))
            adapter = self.registry.get(job.adapter_id)
            scope = repository.scope(job.workspace_id)
            decision = authorize(
                repository.store.workspace(job.workspace_id), scope, job.target, adapter.capability
            )
            repository.store.record_decision(decision)
            if not decision.allowed:
                job = job.model_copy(
                    update={"status": "failed", "error": "scope_no_longer_allowed"}
                )
            else:
                checksum = db.execute(
                    "SELECT input_sha256 FROM job_queue WHERE job_id=?", (str(job.id),)
                ).fetchone()[0]
                metadata = {
                    **output.data,
                    "adapter_id": adapter.id,
                    "adapter_version": adapter.version,
                    "job_id": str(job.id),
                    "queued_input_sha256": checksum,
                    "scope_decision_id": str(decision.id),
                }
                if output.fixture is not None:
                    if scope is None or output.fixture.target != job.target:
                        raise ValueError("Adapter fixture target mismatch")
                    run = import_fixture(
                        repository.store,
                        job.workspace_id,
                        scope,
                        output.fixture,
                        source_metadata=metadata,
                        source_kind="junit_observation"
                        if adapter.id == "junit"
                        else "fixture_observation",
                    )
                    result: dict[str, object] = {
                        "run_id": str(run.id),
                        "findings": len(run.findings),
                    }
                else:
                    artifact = repository.add_artifact(job.workspace_id, output.kind, metadata)
                    result = {"artifact_id": artifact["id"]}
                job = job.model_copy(update={"status": "succeeded", "result": result})
            job = job.model_copy(update={"updated_at": database_now(db)})
            self.write_job(db, job)
            db.execute("DELETE FROM job_queue WHERE job_id=?", (str(job.id),))
            self.audit(db, job, actor, worker_id=self.worker_id)

    def finish_failure(
        self, job: Job, token: str, actor: str, error: str, *, retry: bool = False
    ) -> None:
        with self.repository.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if not self.owns(db, job, token):
                return
            attempts = db.execute(
                "SELECT attempts FROM job_queue WHERE job_id=?", (str(job.id),)
            ).fetchone()[0]
            retry = retry and attempts < self.max_attempts
            job = job.model_copy(
                update={
                    "status": "queued" if retry else "failed",
                    "error": None if retry else error,
                    "updated_at": database_now(db),
                }
            )
            self.write_job(db, job)
            if retry:
                db.execute(
                    "UPDATE job_queue SET owner=NULL,lease_until=NULL WHERE job_id=?",
                    (str(job.id),),
                )
            else:
                db.execute("DELETE FROM job_queue WHERE job_id=?", (str(job.id),))
            self.audit(db, job, actor, reason=error)

    async def execute_claim(
        self, job: Job, payload: dict[str, object], token: str, actor: str
    ) -> None:
        actor_token = current_actor.set(actor)
        try:
            adapter = self.registry.get(job.adapter_id)

            async def produce() -> AdapterOutput:
                if not await adapter.healthcheck():
                    raise ValueError("Adapter unavailable")
                return await adapter.execute(job.target, payload)

            output = await asyncio.wait_for(produce(), timeout=10)
            await asyncio.sleep(0)
            self.persist(job, output, token, actor)
        except asyncio.CancelledError:
            self.finish_failure(job, token, actor, "worker_shutdown", retry=True)
        except TimeoutError:
            self.finish_failure(job, token, actor, "adapter_timeout")
        except Exception:
            self.finish_failure(job, token, actor, "adapter_or_storage_error")
        finally:
            current_actor.reset(actor_token)

    def completed(self, identity: UUID, task: asyncio.Task[None]) -> None:
        if self.tasks.get(identity) is task:
            self.tasks.pop(identity)
        if not task.cancelled() and task.exception() is not None:
            logging.getLogger("aegis.worker").error("Durable worker task failed")

    async def dispatch(self) -> None:
        while not self.stop.is_set():
            try:
                self.recover()
                while len(self.tasks) < 2 and not self.stop.is_set():
                    claim = self.claim()
                    if claim is None:
                        break
                    job = claim[0]
                    task = asyncio.create_task(self.execute_claim(*claim))
                    self.tasks[job.id] = task
                    task.add_done_callback(partial(self.completed, job.id))
            except Exception:
                logging.getLogger("aegis.worker").error("Durable worker dispatch failed")
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=0.25)
            except TimeoutError:
                pass

    async def start(self) -> None:
        self.recover()
        if self.worker:
            self.dispatcher = asyncio.create_task(self.dispatch())

    async def cancel(self, workspace_id: UUID, identity: UUID) -> Job:
        with self.repository.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM jobs WHERE id=? AND workspace_id=?",
                (str(identity), str(workspace_id)),
            ).fetchone()
            if row is None:
                raise ValueError("Job not found in workspace")
            job = Job.model_validate_json(row[0])
            if job.status in {"queued", "running"}:
                job = job.model_copy(
                    update={
                        "status": "cancelled",
                        "error": "cancelled_by_operator",
                        "updated_at": database_now(db),
                    }
                )
                self.write_job(db, job)
                db.execute("DELETE FROM job_queue WHERE job_id=?", (str(identity),))
                self.audit(db, job, current_actor.get())
        if task := self.tasks.get(identity):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return job

    async def shutdown(self) -> None:
        self.stop.set()
        if self.dispatcher:
            await self.dispatcher
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # A task cancelled before its first instruction cannot release its own claim.
        with self.repository.store.connection() as db:
            rows = db.execute(
                "SELECT j.payload,q.owner,q.actor FROM jobs j JOIN job_queue q "
                "ON q.job_id=j.id WHERE q.owner LIKE ? AND j.status='running'",
                (self.worker_id + ":%",),
            ).fetchall()
        for raw, token, actor in rows:
            self.finish_failure(
                Job.model_validate_json(raw), token, actor, "worker_shutdown", retry=True
            )
