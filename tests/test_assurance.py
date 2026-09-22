import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from aegis.assurance import Fixture, compare
from aegis.cli import app
from aegis.demo import run_demo
from aegis.policy import Risk, Scope, Workspace, authorize, normalize_target
from aegis.service import import_fixture, render_report
from aegis.store import Store


def scope_for(workspace: Workspace, **changes: object) -> Scope:
    values = {
        "workspace_id": workspace.id,
        "allowed_targets": ("fixture://documents/read",),
        "capabilities": ("fixture.evaluate", "evidence.import"),
        "starts_at": datetime.now(UTC) - timedelta(hours=1),
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    values.update(changes)
    return Scope.model_validate(values)


def fixture(observed: str = "deny", **changes: object) -> Fixture:
    values = {
        "target": "fixture://documents/read",
        "target_version": "v1",
        "policy_version": "p1",
        "identity_set": "alice-bob",
        "checks": [
            {
                "id": "ownership",
                "title": "Non-owner access",
                "expected": "deny",
                "observed": observed,
                "severity": "high",
            }
        ],
    }
    values.update(changes)
    return Fixture.model_validate(values)


@pytest.mark.parametrize(
    "target",
    [
        "https://example.com",
        "fixture://a/../b",
        "fixture://a//b",
        "fixture://a/%2f",
        "fixture://a/*",
        "fixture://a?x=y",
        "fixture://a#x",
        "fixture://user@a",
        "fixture://a:80",
        "fixture://A",
        "fixture://a/",
        "fixture://a\\b",
        "",
    ],
)
def test_ambiguous_targets_rejected(target: str) -> None:
    with pytest.raises(ValueError):
        normalize_target(target)


def test_policy_precedence() -> None:
    workspace = Workspace(name="test")
    scope = scope_for(workspace)
    assert authorize(workspace, scope, "fixture://documents/read", "fixture.evaluate").allowed
    assert (
        authorize(workspace, None, "fixture://documents/read", "fixture.evaluate").reason
        == "missing_scope"
    )
    excluded = scope_for(workspace, excluded_targets=("fixture://documents/read",))
    assert (
        authorize(workspace, excluded, "fixture://documents/read", "fixture.evaluate").reason
        == "explicit_exclusion"
    )
    assert (
        authorize(workspace, scope, "fixture://unknown", "fixture.evaluate").reason
        == "unknown_target"
    )
    assert not authorize(workspace, scope, "fixture://documents/read", "network.execute").allowed
    assert not authorize(
        workspace, scope, "fixture://documents/read", "fixture.evaluate", Risk.HIGH
    ).allowed
    assert not authorize(
        Workspace(name="other"), scope, "fixture://documents/read", "fixture.evaluate"
    ).allowed
    assert not authorize(
        workspace, scope, "fixture://documents/read", "fixture.evaluate", now=scope.expires_at
    ).allowed
    forensic = Workspace(name="offline", mode="forensics")
    assert not authorize(
        forensic, scope_for(forensic), "fixture://documents/read", "fixture.evaluate"
    ).allowed


def test_invalid_scope_and_duplicate_checks() -> None:
    with pytest.raises(ValidationError):
        scope_for(Workspace(name="test"), starts_at=datetime.now())
    with pytest.raises(ValidationError):
        scope_for(Workspace(name="test"), allowed_targets=("*",))
    check = fixture().checks[0].model_dump()
    with pytest.raises(ValidationError):
        fixture(checks=[check, check])


def test_end_to_end_regression_and_fix(tmp_path: Path) -> None:
    store = Store(tmp_path / "aegis.db")
    workspace = Workspace(name="test")
    store.add_workspace(workspace)
    scope = scope_for(workspace)
    good = import_fixture(store, workspace.id, scope, fixture())
    bad = import_fixture(store, workspace.id, scope, fixture("allow", target_version="v2"))
    fixed = import_fixture(store, workspace.id, scope, fixture(target_version="v3"))
    assert not good.findings
    assert len(bad.findings) == 1
    assert bad.findings[0].evidence_id == bad.evidence_id
    store.baseline(workspace.id, "secure", good.id)
    assert compare(store.get_baseline(workspace.id, "secure"), bad) == {
        "ownership": "new_regression"
    }
    assert compare(bad, fixed) == {"ownership": "fixed"}
    reopened = Store(tmp_path / "aegis.db")
    assert reopened.run(bad.id, workspace.id) == bad
    evidence = reopened.evidence(bad.evidence_id, workspace.id)
    assert evidence["run_id"] == str(bad.id)
    assert "FAIL" in render_report(bad, evidence)
    with pytest.raises(sqlite3.IntegrityError):
        store.baseline(workspace.id, "secure", bad.id)
    with pytest.raises(ValueError):
        store.run(bad.id, uuid4())
    with pytest.raises(ValueError):
        store.evidence(bad.evidence_id, uuid4())
    with pytest.raises(sqlite3.IntegrityError):
        store.save_run(bad, evidence)


def test_denied_run_audited_without_evidence(tmp_path: Path) -> None:
    store = Store(tmp_path / "aegis.db")
    workspace = Workspace(name="test")
    store.add_workspace(workspace)
    with pytest.raises(ValueError, match="denied"):
        import_fixture(store, workspace.id, scope_for(workspace, capabilities=()), fixture())
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM decisions").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0


def test_uncertainty_and_incompatible_baselines(tmp_path: Path) -> None:
    store = Store(tmp_path / "aegis.db")
    workspace = Workspace(name="test")
    store.add_workspace(workspace)
    scope = scope_for(workspace)
    good = import_fixture(store, workspace.id, scope, fixture())
    for observed in ("unknown", "error"):
        run = import_fixture(store, workspace.id, scope, fixture(observed))
        assert run.assertions[0].outcome == observed.upper()
        assert compare(good, run) == {"ownership": "uncertain"}
    incompatible = import_fixture(store, workspace.id, scope, fixture(policy_version="changed"))
    with pytest.raises(ValueError, match="Incompatible"):
        compare(good, incompatible)


def test_tampered_evidence_is_rejected(tmp_path: Path) -> None:
    store = Store(tmp_path / "aegis.db")
    workspace = Workspace(name="test")
    store.add_workspace(workspace)
    run = import_fixture(store, workspace.id, scope_for(workspace), fixture())
    with store.connection() as db:
        db.execute("UPDATE evidence SET payload = '{}' WHERE id = ?", (str(run.evidence_id),))
    with pytest.raises(ValueError, match="integrity"):
        store.evidence(run.evidence_id, workspace.id)


def test_redaction_and_html_escaping(tmp_path: Path) -> None:
    store = Store(tmp_path / "aegis.db")
    workspace = Workspace(name="test")
    store.add_workspace(workspace)
    check = fixture().checks[0].model_dump()
    check["title"] = "<script>alert(1)</script> token=canary Bearer secret123"
    run = import_fixture(store, workspace.id, scope_for(workspace), fixture(checks=[check]))
    evidence = store.evidence(run.evidence_id, workspace.id)
    report = render_report(run, evidence)
    assert "<script>" not in report
    assert "canary" not in report
    assert "secret123" not in report
    assert "REDACTED" in report


def test_cli_fixture_flow(tmp_path: Path) -> None:
    runner = CliRunner()
    database = tmp_path / "aegis.db"
    database_args = ["--database", str(database)]
    result = runner.invoke(app, ["assurance", "workspace-create", "demo", *database_args])
    assert result.exit_code == 0, result.output
    workspace = Workspace.model_validate_json(result.stdout)
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(scope_for(workspace).model_dump_json(), encoding="utf-8")
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(fixture("allow").model_dump_json(), encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "assurance",
            "import-fixture",
            str(workspace.id),
            str(fixture_path),
            str(scope_path),
            *database_args,
        ],
    )
    assert result.exit_code == 1, result.output
    run_id = json.loads(result.stdout)["id"]
    report_path = tmp_path / "report.html"
    args = ["assurance", "report", str(workspace.id), run_id, str(report_path), *database_args]
    assert runner.invoke(app, args).exit_code == 0
    assert report_path.is_file()
    assert runner.invoke(app, args).exit_code == 2


def test_demo_runs_without_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network must not be used")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    output = tmp_path / "demo"
    summary = run_demo(output)
    assert summary["secure_to_regressed"] == {"non-owner-read": "new_regression"}
    assert summary["regressed_to_fixed"] == {"non-owner-read": "fixed"}
    assert (output / "regressed.html").is_file()
    with pytest.raises(FileExistsError):
        run_demo(output)


def test_storage_rejects_mismatched_decision_and_rolls_back(tmp_path: Path) -> None:
    store = Store(tmp_path / "aegis.db")
    workspace = Workspace(name="test")
    store.add_workspace(workspace)
    run = import_fixture(store, workspace.id, scope_for(workspace), fixture())
    evidence = store.evidence(run.evidence_id, workspace.id)
    forged = run.model_copy(
        update={"id": uuid4(), "evidence_id": uuid4(), "target": "fixture://other"}
    )
    with pytest.raises(ValueError, match="does not match"):
        store.save_run(forged, evidence)
    # New evidence insertion rolls back if run insertion fails on a duplicate identifier.
    duplicate = run.model_copy(update={"evidence_id": uuid4()})
    with pytest.raises(sqlite3.IntegrityError):
        store.save_run(duplicate, evidence)
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 1


def test_unknown_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "aegis.db"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version = 999")
    with pytest.raises(ValueError, match="schema"):
        Store(path)


def test_cli_unknown_is_error_not_pass(tmp_path: Path) -> None:
    store = Store(tmp_path / "aegis.db")
    workspace = Workspace(name="test")
    store.add_workspace(workspace)
    scope = tmp_path / "scope.json"
    scope.write_text(scope_for(workspace).model_dump_json(), encoding="utf-8")
    source = tmp_path / "fixture.json"
    source.write_text(fixture("unknown").model_dump_json(), encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "assurance",
            "import-fixture",
            str(workspace.id),
            str(source),
            str(scope),
            "--database",
            str(store.path),
        ],
    )
    assert result.exit_code == 2
    assert json.loads(result.stdout)["assertions"][0]["outcome"] == "UNKNOWN"
