"""Application commands; errors never echo untrusted input."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

import typer

from aegis.assurance import Fixture, compare
from aegis.demo import run_demo
from aegis.gate import GatePolicy, Severity, evaluate_gate
from aegis.junit import load_junit
from aegis.policy import Scope, Workspace
from aegis.service import import_fixture, read_json, render_report
from aegis.store import Store

app = typer.Typer(no_args_is_help=True)
Database = Annotated[Path, typer.Option("--database", help="Local AEGIS SQLite database")]


@app.command("demo")
def demo(output: Path) -> None:
    """Create a new directory with a complete offline regression demonstration."""
    with errors():
        typer.echo(json.dumps(run_demo(output), indent=2))


@contextmanager
def errors() -> Iterator[None]:
    try:
        yield
    except (ValueError, OSError, sqlite3.Error, RecursionError):
        typer.echo("Operation failed: check input, scope, identifiers and local storage.", err=True)
        raise typer.Exit(2) from None


@app.command("workspace-create")
def workspace_create(
    name: str,
    mode: Annotated[str, typer.Option()] = "appsec",
    database: Database = Path(".aegis/aegis.db"),
) -> None:
    """Create a local workspace; output its UUID for subsequent commands."""
    with errors():
        workspace = Workspace.model_validate({"name": name, "mode": mode})
        Store(database).add_workspace(workspace)
        typer.echo(workspace.model_dump_json(indent=2))


@app.command("import-fixture")
def fixture_import(
    workspace_id: UUID,
    fixture: Path,
    scope: Path,
    database: Database = Path(".aegis/aegis.db"),
) -> None:
    """Evaluate offline observations. Exit 1 for failures; 2 for errors/unknowns."""
    with errors():
        run = import_fixture(
            Store(database),
            workspace_id,
            Scope.model_validate(read_json(scope)),
            Fixture.model_validate(read_json(fixture)),
        )
        typer.echo(run.model_dump_json(indent=2))
        if any(a.outcome in ("ERROR", "UNKNOWN") for a in run.assertions):
            raise typer.Exit(2)
        if run.findings:
            raise typer.Exit(1)


@app.command("import-junit")
def junit_import(
    workspace_id: UUID,
    source: Path,
    scope: Path,
    target: Annotated[str, typer.Option()],
    revision: Annotated[str, typer.Option()],
    suite_version: Annotated[str, typer.Option()] = "v1",
    database: Database = Path(".aegis/aegis.db"),
) -> None:
    """Import existing JUnit results without running tests or retaining failure bodies."""
    with errors():
        store = Store(database)
        if store.workspace(workspace_id).mode != "qa":
            raise ValueError("JUnit import requires QA mode")
        run = import_fixture(
            store,
            workspace_id,
            Scope.model_validate(read_json(scope)),
            load_junit(source, target, revision, suite_version),
            source_kind="junit_observation",
        )
        typer.echo(run.model_dump_json(indent=2))
        if any(a.outcome in ("ERROR", "UNKNOWN") for a in run.assertions):
            raise typer.Exit(2)
        if run.findings:
            raise typer.Exit(1)


@app.command("baseline-create")
def baseline_create(
    workspace_id: UUID,
    name: str,
    run_id: UUID,
    database: Database = Path(".aegis/aegis.db"),
) -> None:
    """Save an immutable named baseline; duplicate names are rejected."""
    with errors():
        Store(database).baseline(workspace_id, name, run_id)
        typer.echo("Baseline created")


@app.command("compare")
def compare_runs(
    workspace_id: UUID,
    baseline: str,
    run_id: UUID,
    database: Database = Path(".aegis/aegis.db"),
) -> None:
    """Compare compatible recorded runs."""
    with errors():
        store = Store(database)
        comparison = compare(
            store.get_baseline(workspace_id, baseline), store.run(run_id, workspace_id)
        )
        typer.echo(json.dumps(comparison, indent=2))


@app.command("report")
def report(
    workspace_id: UUID,
    run_id: UUID,
    output: Path,
    format: Annotated[Literal["html", "json"], typer.Option()] = "html",
    database: Database = Path(".aegis/aegis.db"),
) -> None:
    """Export an integrity-checked report; existing files are never overwritten."""
    with errors():
        store = Store(database)
        run = store.run(run_id, workspace_id)
        evidence = store.evidence(run.evidence_id, workspace_id)
        content = (
            render_report(run, evidence)
            if format == "html"
            else json.dumps({"run": run.model_dump(mode="json"), "evidence": evidence}, indent=2)
        )
        with output.open("x", encoding="utf-8") as stream:
            stream.write(content)
        typer.echo("Report written")


@app.command("gate")
def gate(
    workspace_id: UUID,
    baseline: str,
    run_id: UUID,
    minimum_severity: Annotated[Severity, typer.Option()] = "high",
    fail_on_any_violation: Annotated[bool, typer.Option()] = False,
    database: Database = Path(".aegis/aegis.db"),
) -> None:
    """CI gate: exit 0 pass, 1 blocking regression, 2 uncertainty/configuration error."""
    with errors():
        store = Store(database)
        result = evaluate_gate(
            store.get_baseline(workspace_id, baseline),
            store.run(run_id, workspace_id),
            GatePolicy(
                minimum_severity=minimum_severity, fail_on_any_violation=fail_on_any_violation
            ),
        )
        typer.echo(result.model_dump_json(indent=2))
        raise typer.Exit(result.exit_code)
