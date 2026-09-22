import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aegis.adapter_contract import AdapterOutput, Registry
from aegis.adapters import BaseAdapter
from aegis.jobs import Job, JobManager
from aegis.policy import Scope, Workspace
from aegis.repository import Repository
from aegis.store import Store


class SlowAdapter(BaseAdapter):
    id, name = "slow-test", "Slow test adapter"
    modes: tuple[str, ...] = ("appsec",)

    async def execute(self, target: str, payload: dict[str, object]) -> AdapterOutput:
        await asyncio.sleep(30)
        return AdapterOutput(kind="test")


def setup(tmp_path: Path) -> tuple[JobManager, Workspace]:
    repository = Repository(Store(tmp_path / "jobs.db"))
    workspace = Workspace(name="jobs")
    repository.store.add_workspace(workspace)
    now = datetime.now(UTC)
    repository.save_scope(
        Scope(
            workspace_id=workspace.id,
            allowed_targets=("fixture://local/sample",),
            capabilities=("evidence.import",),
            starts_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
        )
    )
    registry = Registry()
    registry.register(SlowAdapter())
    return JobManager(repository, registry), workspace


def test_cancel_running_and_before_start(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, workspace = setup(tmp_path)
        queued = await manager.submit(workspace.id, "slow-test", "fixture://local/sample", {})
        assert (await manager.cancel(workspace.id, queued.id)).status == "cancelled"
        running = await manager.submit(workspace.id, "slow-test", "fixture://local/sample", {})
        await asyncio.sleep(0.01)
        assert manager.get(workspace.id, running.id).status == "running"
        assert (await manager.cancel(workspace.id, running.id)).status == "cancelled"
        assert manager.repository.artifacts(workspace.id) == []

    asyncio.run(scenario())


def test_queue_bound_and_shutdown(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, workspace = setup(tmp_path)
        for _ in range(32):
            await manager.submit(workspace.id, "slow-test", "fixture://local/sample", {})
        try:
            await manager.submit(workspace.id, "slow-test", "fixture://local/sample", {})
        except ValueError as error:
            assert "full" in str(error)
        else:
            raise AssertionError("Queue bound was not enforced")
        await manager.shutdown()
        assert all(job.status == "cancelled" for job in manager.list(workspace.id))

    asyncio.run(scenario())


def test_scope_rechecked_and_restart_recovery(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager, workspace = setup(tmp_path)
        job = await manager.submit(workspace.id, "slow-test", "fixture://local/sample", {})
        scope = manager.repository.scope(workspace.id)
        assert scope is not None
        manager.repository.save_scope(scope.model_copy(update={"capabilities": ()}))
        await asyncio.sleep(0.03)
        assert manager.get(workspace.id, job.id).status == "failed"
        interrupted = Job(
            workspace_id=workspace.id, adapter_id="slow-test", target="fixture://local/sample"
        )
        manager.save(interrupted)
        manager.recover()
        assert manager.get(workspace.id, interrupted.id).error == "interrupted_by_restart"
        await manager.shutdown()

    asyncio.run(scenario())


def test_scope_revocation_during_execution_prevents_result_storage(tmp_path: Path) -> None:
    async def scenario() -> None:
        started, release = asyncio.Event(), asyncio.Event()

        class ControlledAdapter(SlowAdapter):
            id = "controlled-test"

            async def execute(self, target: str, payload: dict[str, object]) -> AdapterOutput:
                started.set()
                await release.wait()
                return AdapterOutput(kind="test")

        manager, workspace = setup(tmp_path)
        manager.registry.register(ControlledAdapter())
        job = await manager.submit(workspace.id, "controlled-test", "fixture://local/sample", {})
        await started.wait()
        scope = manager.repository.scope(workspace.id)
        assert scope is not None
        manager.repository.save_scope(scope.model_copy(update={"capabilities": ()}))
        release.set()
        await asyncio.gather(*manager.tasks.values())
        assert manager.get(workspace.id, job.id).status == "failed"
        assert manager.repository.artifacts(workspace.id) == []
        await manager.shutdown()

    asyncio.run(scenario())
