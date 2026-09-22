"""Small reproducible development load sample; never opens the user's database."""

import json
import statistics
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from aegis.api import create_app

with tempfile.TemporaryDirectory(prefix="aegis-load-") as directory:
    app = create_app(Path(directory) / "load.db", "disposable-load-check-token-000000000000")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.headers["Authorization"] = "Bearer disposable-load-check-token-000000000000"
        workspace = client.post("/api/workspaces", json={"name": "Load smoke", "mode": "qa"}).json()["id"]
        repository = app.state.repository
        for index in range(200):
            repository.add_artifact(UUID(workspace), "note", {"note": "Load record " + str(index)})
        def sample(index: int) -> float:
            start = time.perf_counter()
            response = client.get(f"/api/workspaces/{workspace}/artifacts", params={"limit": 50, "offset": index % 4 * 50})
            assert response.status_code == 200 and len(response.json()) == 50
            return (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=4) as executor:
            samples = list(executor.map(sample, range(100)))
        result = {"timestamp": datetime.now(UTC).isoformat(), "scope": "In-process development read smoke; not production capacity",
                  "requests": len(samples), "concurrency": 4, "stored_artifacts": 200,
                  "elapsed_seconds": round(time.perf_counter() - start, 3),
                  "median_ms": round(statistics.median(samples), 3),
                  "p95_ms": round(sorted(samples)[94], 3), "max_ms": round(max(samples), 3),
                  "failures": 0}
        output = Path(".aegis/verification/load.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
