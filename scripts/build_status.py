"""Update the local visible build feed, recording actual completed work only."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

path = Path(__file__).resolve().parents[1] / ".aegis/live/status.json"
path.parent.mkdir(parents=True, exist_ok=True)
parser = argparse.ArgumentParser()
parser.add_argument("current")
parser.add_argument("--detail", default="")
parser.add_argument("--done", type=int, action="append", default=[])
parser.add_argument("--active", type=int)
parser.add_argument("--app-url")
parser.add_argument("--state", default="building")
args = parser.parse_args()
if path.exists():
    status = json.loads(path.read_text(encoding="utf-8"))
else:
    status = {"phases": [{"name": name, "status": "pending"} for name in [
        "Package and CLI foundation", "Workspace, modes and policy", "Evidence and persistence",
        "Invariants and comparisons", "Adapter contracts and imports", "Authenticated local API",
        "Working web dashboard", "Offline analysis modes", "Jobs and cancellation",
        "Retention and audit", "Packaging and deployment", "End-to-end verification and docs",
    ]], "events": []}
now = datetime.now(UTC)
status.update(updated_at=now.isoformat(), current=args.current, detail=args.detail,
              running=args.state == "building", state=args.state)
for index in args.done:
    status["phases"][index]["status"] = "done"
if args.active is not None:
    status["phases"][args.active]["status"] = "active"
if args.app_url:
    status["app_url"] = args.app_url
status["events"].append({"time": now.astimezone().strftime("%H:%M:%S"), "message": args.current})
temp = path.with_suffix(".tmp")
temp.write_text(json.dumps(status, indent=2), encoding="utf-8")
temp.replace(path)
