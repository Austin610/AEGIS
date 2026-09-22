"""Local server launcher with an OS-user-owned access token."""

import os
import secrets
from pathlib import Path

import uvicorn

from aegis.api import create_app


def serve(data_dir: Path, port: int, *, container: bool = False) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    token_path = data_dir / "access-token"
    if not token_path.exists():
        descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(secrets.token_urlsafe(32))
    token = token_path.read_text(encoding="utf-8").strip()
    application = create_app(
        os.environ.get("AEGIS_DATABASE_URL") or data_dir / "aegis.db",
        token,
        durable_jobs=os.environ.get("AEGIS_DURABLE_JOBS") == "1",
        deletion_grace_days=(
            int(os.environ["AEGIS_DELETION_GRACE_DAYS"])
            if os.environ.get("AEGIS_DELETION_GRACE_DAYS")
            else None
        ),
        pg_recovery_bin=(
            Path(os.environ["AEGIS_POSTGRES_BIN"]) if os.environ.get("AEGIS_POSTGRES_BIN") else None
        ),
        recovery_dir=data_dir / "deletion-recovery",
        recovery_retention_days=(
            int(os.environ["AEGIS_RECOVERY_RETENTION_DAYS"])
            if os.environ.get("AEGIS_RECOVERY_RETENTION_DAYS")
            else None
        ),
        embedded_worker=os.environ.get("AEGIS_EMBEDDED_WORKER", "1") == "1",
    )
    uvicorn.run(
        application,
        host="0.0.0.0" if container else "127.0.0.1",
        port=port,
        access_log=False,
        limit_concurrency=64,
        timeout_keep_alive=5,
    )
