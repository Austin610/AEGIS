"""Exercise an isolated disposable container's health, authentication and restart persistence."""

import json
import subprocess
import time
import urllib.request
from uuid import uuid4

name = "aegis-smoke-" + uuid4().hex
volume = name + "-data"


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True).strip()


def ready() -> str:
    port = docker("port", name, "8766/tcp").split(":")[-1]
    root = "http://127.0.0.1:" + port
    for _ in range(60):
        try:
            with urllib.request.urlopen(root + "/health", timeout=2) as response:
                assert json.load(response)["status"] == "ok"
                return root
        except OSError:
            time.sleep(0.5)
    raise RuntimeError("Container health did not become ready")


def request(root: str, path: str, token: str, body: dict | None = None) -> object:
    request = urllib.request.Request(root + path, data=json.dumps(body).encode() if body else None,
                                    headers={"Authorization": "Bearer " + token,
                                             "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


try:
    docker("volume", "create", volume)
    docker("run", "-d", "--name", name, "-p", "127.0.0.1::8766", "-v", volume + ":/data", "aegis-local")
    root = ready()
    token = docker("exec", name, "cat", "/data/access-token")
    created = request(root, "/api/workspaces", token, {"name": "Container persistence check", "mode": "qa"})
    docker("restart", name)
    root = ready()
    restored = request(root, "/api/workspaces", token)
    assert isinstance(restored, list) and created in restored
    print("Container health, authentication and restart persistence passed")
finally:
    subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)
    subprocess.run(["docker", "volume", "rm", volume], check=False, capture_output=True)
