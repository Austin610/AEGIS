import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from aegis.access import Access, NewKey
from aegis.assurance import Check, Fixture
from aegis.data_policy import DataPolicies, DataPolicy
from aegis.planning import Planning, WorkItem
from aegis.policy import Scope, Workspace
from aegis.repository import Repository
from aegis.service import import_fixture
from aegis.store import Store
from aegis.transfer import transfer


@pytest.mark.skipif(
    not os.environ.get("AEGIS_TEST_POSTGRES_URL"), reason="PostgreSQL test URL required"
)
def test_sqlite_transfer_preserves_graph_and_rejects_nonempty_destination(
    client, tmp_path: Path
) -> None:
    source = Store(tmp_path / "source.db")
    workspace = Workspace(name="Migrated workspace")
    source.add_workspace(workspace)
    repository = Repository(source)
    scope = Scope(
        workspace_id=workspace.id,
        allowed_targets=("fixture://local/sample",),
        capabilities=("fixture.evaluate", "evidence.import"),
        starts_at=datetime.now(UTC) - timedelta(days=1),
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    repository.save_scope(scope)
    run = import_fixture(
        source,
        workspace.id,
        scope,
        Fixture(
            target="fixture://local/sample",
            target_version="v1",
            policy_version="p1",
            identity_set="a",
            checks=(Check(id="check", title="Check", expected="deny", observed="deny"),),
        ),
    )
    source.baseline(workspace.id, "release", run.id)
    artifact = repository.add_artifact(workspace.id, "note", {"note": "Migration verification"})
    key = Access(source).create(
        NewKey(label="Migrated reader", role="reader", workspace_ids=(workspace.id,))
    )
    DataPolicies(source).save(workspace.id, DataPolicy(archive_after_days=30), "owner")
    Planning(repository).save(workspace.id, "item", uuid4(), WorkItem(title="Transferred story"))
    target = client.app.state.repository
    counts = transfer(source.path, target.store.database_url)
    assert counts["runs"] == 1
    assert counts["artifacts"] == 1
    assert counts["workflow_records"] == 1
    assert Planning(target).snapshot(workspace.id)["items"][0]["title"] == "Transferred story"
    assert target.store.run(run.id, workspace.id) == run
    assert target.store.evidence(run.evidence_id, workspace.id) == source.evidence(
        run.evidence_id, workspace.id
    )
    assert target.artifacts(workspace.id) == [artifact]
    assert target.store.get_baseline(workspace.id, "release") == run
    assert DataPolicies(target.store).get(workspace.id).archive_after_days == 30
    assert Access(target.store).lookup(str(key["token"])).role == "reader"
    with pytest.raises(ValueError, match="empty"):
        transfer(source.path, target.store.database_url)
    assert len(target.runs(workspace.id)) == 1
    assert repository.artifacts(workspace.id) == [artifact]
