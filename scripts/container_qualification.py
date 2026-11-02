"""Disposable local Docker workload and verified SQLite restore drill."""

import json
import secrets
import subprocess
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from aegis.backup import restore, snapshot

PILOT_P95_READ_MS = 250
PILOT_RESTORE_MS = 30_000


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True).strip()


def wait_for(name: str) -> tuple[str, float]:
    began = time.perf_counter()
    port = docker("port", name, "8766/tcp").rsplit(":", 1)[-1]
    root = "http://127.0.0.1:" + port
    for _ in range(100):
        try:
            with urllib.request.urlopen(root + "/health", timeout=2) as response:
                health = json.load(response)
            if health["status"] == "ok" and health["schema"] == 6:
                return root, (time.perf_counter() - began) * 1000
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("Disposable Docker container did not become healthy")


def request(
    root: str,
    token: str,
    body: dict | None = None,
    path: str = "/api/workspaces",
    method: str | None = None,
) -> object:
    req = urllib.request.Request(
        root + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def run() -> dict:
    suffix = uuid4().hex[:12]
    source_name, restored_name = "aegis-qual-src-" + suffix, "aegis-qual-rest-" + suffix
    with tempfile.TemporaryDirectory(prefix="aegis-docker-qualification-") as temporary:
        base = Path(temporary)
        source, target = base / "source", base / "restored"
        source.mkdir()
        target.mkdir()
        token = secrets.token_urlsafe(32)
        (source / "access-token").write_text(token, encoding="utf-8")
        try:
            for name, data in ((source_name, source),):
                docker(
                    "run",
                    "-d",
                    "-e",
                    "AEGIS_DURABLE_JOBS=1",
                    "--name",
                    name,
                    "-p",
                    "127.0.0.1::8766",
                    "-v",
                    str(data.resolve()) + ":/data",
                    "aegis-local",
                )
            root, startup_ms = wait_for(source_name)
            assert request(root, token) == []
            with ThreadPoolExecutor(max_workers=4) as pool:
                created = list(
                    pool.map(
                        lambda index: request(
                            root,
                            token,
                            {
                                "name": "Qualification workspace " + str(index),
                                "mode": "qa",
                            },
                        ),
                        range(12),
                    )
                )
            ids = {item["id"] for item in created}
            workspace = created[0]["id"]
            path = "/api/workspaces/" + workspace
            now = datetime.now(UTC)
            request(
                root,
                token,
                {
                    "workspace_id": workspace,
                    "allowed_targets": ["fixture://local/sample"],
                    "capabilities": ["evidence.import", "fixture.evaluate"],
                    "starts_at": (now - timedelta(minutes=1)).isoformat(),
                    "expires_at": (now + timedelta(hours=1)).isoformat(),
                },
                path=path + "/scope",
                method="PUT",
            )

            def submit(index: int) -> str:
                result = request(
                    root,
                    token,
                    {
                        "adapter_id": "notes",
                        "target": "fixture://local/sample",
                        "payload": {"title": "Container drill", "note": f"Observation {index}"},
                    },
                    path=path + "/jobs",
                )
                return result["id"]

            with ThreadPoolExecutor(max_workers=4) as pool:
                submitted = set(pool.map(submit, range(12)))
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                jobs = request(root, token, path=path + "/jobs")
                if len(jobs) == 12 and all(job["status"] == "succeeded" for job in jobs):
                    break
                time.sleep(0.1)
            assert {job["id"] for job in jobs} == submitted
            assert all(job["status"] == "succeeded" for job in jobs)
            assert len(request(root, token, path=path + "/artifacts")) == 12

            def read(_: int) -> float:
                began = time.perf_counter()
                items = request(root, token)
                assert {item["id"] for item in items} == ids
                return (time.perf_counter() - began) * 1000

            with ThreadPoolExecutor(max_workers=4) as pool:
                timings = list(pool.map(read, range(100)))
            docker("stop", source_name)
            began = time.perf_counter()
            backup = snapshot(source / "aegis.db", base / "checkpoint.db")
            backup_ms = (time.perf_counter() - began) * 1000
            began = time.perf_counter()
            restore(base / "checkpoint.db", target / "aegis.db", str(backup["sha256"]))
            (target / "access-token").write_text(token, encoding="utf-8")
            docker(
                "run",
                "-d",
                "-e",
                "AEGIS_DURABLE_JOBS=1",
                "--name",
                restored_name,
                "-p",
                "127.0.0.1::8766",
                "-v",
                str(target.resolve()) + ":/data",
                "aegis-local",
            )
            restored_root, _ = wait_for(restored_name)
            assert {item["id"] for item in request(restored_root, token)} == ids
            assert {
                job["id"] for job in request(restored_root, token, path=path + "/jobs")
            } == submitted
            assert len(request(restored_root, token, path=path + "/artifacts")) == 12
            restore_verified_ms = (time.perf_counter() - began) * 1000
            p95_read_ms = sorted(timings)[94]
            assert p95_read_ms <= PILOT_P95_READ_MS, "Pilot read latency target missed"
            assert restore_verified_ms <= PILOT_RESTORE_MS, "Pilot restore target missed"
            return {
                "timestamp": datetime.now(UTC).isoformat(),
                "status": "passed",
                "scope": "Disposable local Docker/SQLite fixture",
                "workspaces": len(ids),
                "durable_jobs": len(submitted),
                "reads": len(timings),
                "concurrency": 4,
                "p95_read_ms": round(p95_read_ms, 3),
                "startup_ms": round(startup_ms, 3),
                "backup_ms": round(backup_ms, 3),
                "backup_bytes": backup["bytes"],
                "restore_verified_ms": round(restore_verified_ms, 3),
                "pilot_targets": {
                    "concurrent_requests": 4,
                    "durable_jobs": 12,
                    "p95_read_ms_max": PILOT_P95_READ_MS,
                    "restore_verified_ms_max": PILOT_RESTORE_MS,
                    "checkpoint_record_loss_max": 0,
                },
                "checkpoint_record_loss": 0,
                "notice": "Synthetic local measurements; no production RTO/RPO guarantee.",
            }
        finally:
            for name in (source_name, restored_name):
                subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)


if __name__ == "__main__":
    result = run()
    destination = Path(".aegis/verification/container-qualification.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
