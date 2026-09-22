"""Read-only diagnostics for the M0 foundation."""

import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer

from aegis import __version__
from aegis.commands import app as assurance_app
from aegis.config import ConfigurationError, load_settings
from aegis.logging import configure_logging, run_context

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
app.add_typer(assurance_app, name="assurance", help="Offline assurance workflows")


@app.command("check-github")
def check_github_command() -> None:
    """Check local OAuth settings without displaying secrets or contacting GitHub."""
    from aegis.github_setup import check_github_setup

    result = check_github_setup()
    typer.echo(json.dumps(result, indent=2))
    if result["status"] != "ready_for_live_login":
        raise typer.Exit(1)


@app.command("serve")
def serve_command(
    data_dir: Annotated[Path, typer.Option()] = Path(".aegis/app"),
    port: Annotated[int, typer.Option(min=1024, max=65535)] = 8766,
    container: Annotated[
        bool, typer.Option(help="Bind inside a container; publish only on localhost")
    ] = False,
) -> None:
    """Start the local dashboard. Access token lives in DATA_DIR/access-token."""
    from aegis.server import serve

    serve(data_dir, port, container=container)


@app.command("worker")
def worker_command(
    data_dir: Annotated[Path, typer.Option()] = Path(".aegis/app"),
) -> None:
    """Run a durable offline worker against an explicitly enabled database."""
    import asyncio
    import os

    from aegis.adapters import default_registry
    from aegis.durable_jobs import DurableJobManager
    from aegis.repository import Repository
    from aegis.store import Store

    if os.environ.get("AEGIS_DURABLE_JOBS") != "1":
        raise typer.BadParameter("Set AEGIS_DURABLE_JOBS=1 before starting a worker")
    store = Store(os.environ.get("AEGIS_DATABASE_URL") or data_dir / "aegis.db")
    manager = DurableJobManager(Repository(store), default_registry())

    async def run() -> None:
        await manager.start()
        try:
            await asyncio.Event().wait()
        finally:
            await manager.shutdown()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


def show_version(value: bool) -> None:
    if value:
        typer.echo(f"AEGIS {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=show_version, is_eager=True)
    ] = False,
) -> None:
    """AEGIS local assurance workbench."""


@app.command()
def doctor(config: Annotated[Path | None, typer.Option("--config")] = None) -> None:
    """Validate configuration and report local prerequisites without running tools."""
    try:
        settings = load_settings(config)
    except ConfigurationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from None
    logger = configure_logging(settings.log_level)
    with run_context(uuid4()):
        typer.echo(
            json.dumps(
                {
                    "status": "ok",
                    "version": __version__,
                    "python": sys.version.split()[0],
                    "configuration": "valid",
                    "git_on_path": shutil.which("git") is not None,
                    "docker_on_path": shutil.which("docker") is not None,
                    "database_configured": settings.database_url is not None,
                    "note": "M0 diagnostics only; Docker daemon and database not contacted",
                },
                indent=2,
            )
        )
        logger.info("doctor.completed")


@app.command("backup")
def backup_command(source: Path, destination: Path) -> None:
    """Create a verified SQLite snapshot at a new destination; print its SHA-256."""
    from aegis.backup import snapshot

    try:
        typer.echo(json.dumps(snapshot(source, destination), indent=2))
    except (OSError, ValueError, sqlite3.Error) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from None


@app.command("restore")
def restore_command(
    source: Path, destination: Path, sha256: Annotated[str, typer.Option()]
) -> None:
    """Verify a backup checksum and restore to a new database file."""
    from aegis.backup import restore

    try:
        typer.echo(json.dumps(restore(source, destination, sha256), indent=2))
    except (OSError, ValueError, sqlite3.Error) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from None


@app.command("migrate-postgres")
def migrate_postgres_command(source: Path) -> None:
    """Copy SQLite data to an empty PostgreSQL database in AEGIS_DATABASE_URL."""
    from aegis.transfer import transfer

    try:
        result = transfer(source, os.environ.get("AEGIS_DATABASE_URL", ""))
        typer.echo(json.dumps(result, indent=2))
    except (OSError, ValueError, sqlite3.Error) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from None


@app.command("verify-postgres-recovery")
def postgres_recovery_command(destination: Path, bin_dir: Annotated[Path, typer.Option()]) -> None:
    """Dump AEGIS_DATABASE_URL and verify restoration in a new temporary database."""
    from aegis.pg_recovery import verify_recovery

    url = os.environ.get("AEGIS_DATABASE_URL", "")
    if not url:
        typer.echo("Set AEGIS_DATABASE_URL before verifying recovery", err=True)
        raise typer.Exit(code=2)
    try:
        typer.echo(json.dumps(verify_recovery(url, destination, bin_dir), indent=2))
    except (OSError, ValueError, ImportError):
        typer.echo(
            "Recovery verification failed; inspect configuration and retained archives", err=True
        )
        raise typer.Exit(code=2) from None
