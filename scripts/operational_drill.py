"""Disposable real-process SQLite restart/restore drill; never uses the live database."""

import json
import os
import secrets
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aegis.backup import restore, snapshot
from aegis.policy import Workspace
from aegis.store import MIGRATIONS, SCHEMA_VERSION


def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="aegis-operational-") as temporary:
        root = Path(temporary)
        token = secrets.token_urlsafe(32)
        data = root / "source"
        data.mkdir()
        seed = Workspace(name="Pre-upgrade workspace", mode="qa")
        with closing(sqlite3.connect(data / "aegis.db")) as db:
            for version in range(1, SCHEMA_VERSION):
                db.executescript(MIGRATIONS[version])
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION - 1}")
            db.execute(
                "INSERT INTO workspaces VALUES (?,?)", (str(seed.id), seed.model_dump_json())
            )
            db.commit()
        (data / "access-token").write_text(token, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith("AEGIS_")}
        env["AEGIS_DURABLE_JOBS"] = "1"
        process = None
        base = ""
        log = (root / "server.log").open("wb")

        def request(path, body=None, method=None, credential=token, expected=200):
            req = urllib.request.Request(
                base + path,
                data=json.dumps(body).encode() if body is not None else None,
                method=method,
                headers={
                    "Authorization": "Bearer " + credential,
                    "Content-Type": "application/json",
                },
            )
            try:
                response = urllib.request.urlopen(req, timeout=10)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                assert response.status == expected, (
                    f"Unexpected status for {path}: {response.status}"
                )
                return json.load(response)

        def stop():
            nonlocal process
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                process = None

        def start(directory):
            nonlocal process, base
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            base = f"http://127.0.0.1:{port}"
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "aegis",
                    "serve",
                    "--data-dir",
                    str(directory),
                    "--port",
                    str(port),
                ],
                env=env,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                assert process.poll() is None, "Disposable server exited before readiness"
                try:
                    health = request("/health")
                    if health["job_mode"] == "durable":
                        request("/api/workspaces")  # Ensure this is our authenticated server.
                        return
                except OSError:
                    time.sleep(0.1)
            raise RuntimeError("Disposable server did not become ready")

        try:
            start(data)
            assert any(w["id"] == str(seed.id) for w in request("/api/workspaces"))
            assert request("/health")["schema"] == SCHEMA_VERSION
            request("/api/workspaces", credential="invalid", expected=401)
            workspace = request(
                "/api/workspaces", {"name": "Operational drill", "mode": "qa"}, expected=201
            )["id"]
            path = f"/api/workspaces/{workspace}"
            now = datetime.now(UTC)
            request(
                path + "/scope",
                {
                    "workspace_id": workspace,
                    "allowed_targets": ["fixture://local/sample"],
                    "capabilities": ["evidence.import", "fixture.evaluate"],
                    "starts_at": (now - timedelta(minutes=1)).isoformat(),
                    "expires_at": (now + timedelta(hours=1)).isoformat(),
                },
                "PUT",
            )

            def submit(index):
                return request(
                    path + "/jobs",
                    {
                        "adapter_id": "notes",
                        "target": "fixture://local/sample",
                        "payload": {"title": "Drill", "note": f"Recorded observation {index}"},
                    },
                    expected=202,
                )["id"]

            with ThreadPoolExecutor(max_workers=4) as executor:
                submitted = set(executor.map(submit, range(12)))
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                jobs = request(path + "/jobs")
                if all(j["status"] == "succeeded" for j in jobs) and len(jobs) == 12:
                    break
                time.sleep(0.1)
            assert {j["id"] for j in jobs} == submitted
            assert all(j["status"] == "succeeded" for j in jobs)
            assert len(request(path + "/artifacts")) == 12
            key = request(
                "/api/access-keys",
                {"label": "Drill reader", "role": "reader", "workspace_ids": [workspace]},
                expected=201,
            )
            request("/api/access-keys/" + key["id"], method="DELETE")

            def read_sample(_):
                began = time.perf_counter()
                assert len(request(path + "/artifacts")) == 12
                return (time.perf_counter() - began) * 1000

            with ThreadPoolExecutor(max_workers=4) as executor:
                timings = list(executor.map(read_sample, range(100)))
            stop()
            start(data)
            assert len(request(path + "/artifacts")) == 12
            request("/api/workspaces", credential=key["token"], expected=401)
            stop()
            backup = snapshot(data / "aegis.db", root / "checkpoint.db")
            restored = root / "restored"
            restored.mkdir()
            restore(root / "checkpoint.db", restored / "aegis.db", backup["sha256"])
            (restored / "access-token").write_text(token, encoding="utf-8")
            start(restored)
            assert len(request(path + "/artifacts")) == 12
            assert {j["id"] for j in request(path + "/jobs")} == submitted
            request("/api/workspaces", credential=key["token"], expected=401)
            return {
                "timestamp": datetime.now(UTC).isoformat(),
                "status": "passed",
                "scope": "Loopback real-process SQLite drill, not production capacity",
                "jobs": 12,
                "concurrency": 4,
                "http_reads": 100,
                "median_ms": round(statistics.median(timings), 3),
                "p95_ms": round(sorted(timings)[94], 3),
                "checks": [
                    "previous-schema upgrade preserves existing workspace",
                    "authenticated HTTP",
                    "durable job commits",
                    "restart persistence",
                    "verified restore into new directory",
                    "revocation survives restart and restore",
                ],
            }
        finally:
            stop()
            log.close()


if __name__ == "__main__":
    result = run()
    destination = Path(".aegis/verification/operational-drill.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
