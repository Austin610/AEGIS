import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from typer.testing import CliRunner

from aegis.cli import app
from aegis.junit import load_junit
from aegis.policy import Scope, Workspace
from aegis.store import Store


def test_junit_outcomes(tmp_path: Path) -> None:
    source = tmp_path / "results.xml"
    source.write_text(
        """<testsuites><testsuite name="suite">
      <testcase name="pass" classname="checkout"/>
      <testcase name="fail"><failure>token=secret</failure></testcase>
      <testcase name="error"><error>password=secret</error></testcase>
      <testcase name="skip"><skipped/></testcase>
    </testsuite></testsuites>""",
        encoding="utf-8",
    )
    result = load_junit(source, "fixture://qa/suite", "commit-1", "suite-v1")
    assert [c.observed for c in result.checks] == ["allow", "deny", "error", "unknown"]
    assert "secret" not in result.model_dump_json()


@pytest.mark.parametrize(
    "text",
    [
        '<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///secret">]><testsuite/>',
        '<testsuite><testcase name="x"><error/><failure/></testcase></testsuite>',
        "<testsuite><testcase/></testsuite>",
        "<testsuite/>",
        '<other><testcase name="x"/></other>',
        '<testsuite><testcase name="x"/><testcase name="x"/></testsuite>',
        "<testsuite>",
    ],
)
def test_invalid_junit_rejected(tmp_path: Path, text: str) -> None:
    path = tmp_path / "test.xml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_junit(path, "fixture://qa/suite", "v1", "v1")


def test_junit_cli_stores_provenance(tmp_path: Path) -> None:
    store = Store(tmp_path / "aegis.db")
    workspace = Workspace(name="QA", mode="qa")
    store.add_workspace(workspace)
    now = datetime.now(UTC)
    scope = Scope(
        workspace_id=workspace.id,
        allowed_targets=("fixture://qa/suite",),
        capabilities=("fixture.evaluate",),
        starts_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
    )
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(scope.model_dump_json(), encoding="utf-8")
    source = tmp_path / "results.xml"
    source.write_text('<testsuite><testcase name="pass"/></testsuite>', encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "assurance",
            "import-junit",
            str(workspace.id),
            str(source),
            str(scope_path),
            "--target",
            "fixture://qa/suite",
            "--revision",
            "commit-1",
            "--database",
            str(store.path),
        ],
    )
    assert result.exit_code == 0, result.output
    run = json.loads(result.stdout)
    evidence = store.evidence(UUID(run["evidence_id"]), workspace.id)
    assert evidence["kind"] == "junit_observation"
