"""Reviewed expiry of registered recovery files; arbitrary backups are never enrolled."""

import hashlib
import json
import re
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aegis.backup import checksum
from aegis.repository import Repository
from aegis.store import TransactionStore

if TYPE_CHECKING:
    from aegis.deletion import DeletionManager


class RecoveryRetention:
    def __init__(self, manager: "DeletionManager", days: int | None = None) -> None:
        if days is not None and not 1 <= days <= 36500:
            raise ValueError("Recovery retention must be 1-36500 days")
        self.manager = manager
        self.days = days
        self.tokens: dict[str, dict[str, Any]] = {}

    @property
    def directory(self) -> Path | None:
        if self.manager.store.database_url:
            return self.manager.recovery_dir
        return self.manager.store.path.parent / "deletion-recovery"

    def path(self, name: str) -> Path:
        root = self.directory
        if root is None or root.is_symlink() or root.is_junction():
            raise ValueError("Recovery directory unavailable")
        if not re.fullmatch(r"[a-f0-9]{32}\.(backup\.db|restored\.db|pgdump)", name):
            raise ValueError("Invalid managed recovery name")
        path = root / name
        if path.is_symlink() or path.is_junction() or path.resolve().parent != root.resolve():
            raise ValueError("Recovery file escapes managed directory")
        return path

    @property
    def directory_id(self) -> str:
        return (
            hashlib.sha256(str(self.directory.resolve()).encode()).hexdigest()
            if self.directory
            else ""
        )

    def register(self, paths: list[Path], primary_hash: str, actor: str) -> None:
        files = []
        for path in paths:
            managed = self.path(path.name)
            if managed.resolve() != path.resolve():
                raise ValueError("Recovery registration path mismatch")
            files.append(
                {"name": path.name, "sha256": checksum(managed), "bytes": managed.stat().st_size}
            )
        Repository(self.manager.store).audit(
            None,
            "recovery.verified",
            {
                "actor": actor,
                "directory_id": self.directory_id,
                "files": files,
                "primary_sha256": primary_hash,
            },
        )

    def _records(self, db: Any) -> list[dict[str, Any]]:
        rows = db.execute(
            "SELECT id,created_at,payload FROM audit_events "
            "WHERE action='recovery.verified' ORDER BY rowid DESC LIMIT 129"
        ).fetchall()
        if len(rows) > 128:
            raise ValueError("Recovery inventory exceeds 128 receipts; operator review required")
        records, budget = [], 512 * 1024 * 1024
        seen: set[str] = set()
        active = {
            a["backup_sha256"]
            for a in self.manager.approvals.values()
            if a["deadline"] > time.monotonic()
        }
        held = (
            db.execute(
                "SELECT 1 FROM workflow_records WHERE kind IN "
                "('hold','legal_hold','operational_hold') LIMIT 1"
            ).fetchone()
            is not None
        )
        newest = None
        for receipt, created_at, raw in rows:
            metadata = json.loads(raw)
            if metadata["directory_id"] != self.directory_id:
                continue
            group = []
            created = datetime.fromisoformat(created_at)
            for item in metadata["files"]:
                if item["name"] in seen:
                    raise ValueError("Duplicate recovery registration; operator review required")
                seen.add(item["name"])
                reasons = []
                path = self.path(item["name"])
                if not path.exists():
                    reasons.append("file_absent")
                else:
                    stat = path.stat()
                    budget -= stat.st_size
                    if budget < 0:
                        raise ValueError("Recovery inventory exceeds 512 MiB")
                    if not path.is_file() or stat.st_nlink != 1 or stat.st_size != item["bytes"]:
                        reasons.append("file_changed")
                    elif checksum(path) != item["sha256"]:
                        reasons.append("file_changed")
                group.append(
                    {**item, "receipt_id": receipt, "created_at": created_at, "blockers": reasons}
                )
            if newest is None and group and all(not r["blockers"] for r in group):
                newest = receipt
            for record in group:
                if self.days is None:
                    record["blockers"].append("expiry_disabled")
                elif created > datetime.now(UTC) - timedelta(days=self.days):
                    record["blockers"].append("retention_period")
                if receipt == newest:
                    record["blockers"].append("newest_verified_recovery")
                if metadata["primary_sha256"] in active:
                    record["blockers"].append("active_deletion_approval")
                if held:
                    record["blockers"].append("workspace_hold")
                record["eligible"] = not record["blockers"]
            records.extend(group)
        # If no complete recovery generation survives, no expiry can proceed.
        if newest is None:
            for record in records:
                record["blockers"].append("no_complete_recovery")
                record["eligible"] = False
        return records

    def _reconcile(self, db: Any) -> None:
        rows = db.execute(
            "SELECT id,action,payload FROM audit_events WHERE action IN "
            "('recovery.expiry.requested','recovery.expiry.completed') "
            "ORDER BY rowid DESC LIMIT 1025"
        ).fetchall()
        if len(rows) > 1024:
            raise ValueError("Expiry audit inventory exceeds limit; operator review required")
        completed = {
            json.loads(raw)["request_id"]
            for _, action, raw in rows
            if action == "recovery.expiry.completed"
        }
        for identity, action, raw in rows:
            metadata = json.loads(raw)
            if action != "recovery.expiry.requested" or identity in completed:
                continue
            if metadata["directory_id"] != self.directory_id:
                continue
            present = self.path(metadata["name"]).exists()
            Repository(TransactionStore(db)).audit(
                None,
                "recovery.expiry.completed",
                {
                    "actor": "recovery-reconciliation",
                    "request_id": identity,
                    "outcome": "file_present_requires_new_review"
                    if present
                    else "file_absent_on_reconciliation",
                },
            )

    @staticmethod
    def fingerprint(records: list[dict[str, Any]], days: int | None) -> str:
        return hashlib.sha256(json.dumps([days, records], sort_keys=True).encode()).hexdigest()

    def preview(self) -> dict[str, Any]:
        with self.manager.lock, self.manager.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._reconcile(db)
            records = self._records(db)
            activity = db.execute(
                "SELECT created_at,action,payload FROM audit_events WHERE action IN "
                "('recovery.expiry.requested','recovery.expiry.completed') "
                "ORDER BY rowid DESC LIMIT 100"
            ).fetchall()
            return {
                "records": records,
                "retention_days": self.days,
                "fingerprint": self.fingerprint(records, self.days),
                "recent_activity": [
                    {"created_at": t, "action": a, "metadata": json.loads(raw)}
                    for t, a, raw in activity
                ],
                "notice": "Only registered recovery files are managed. Newest complete recovery, "
                "active deletion approvals and holds are protected. Exports and older unmanaged "
                "backups are unaffected. Expiry requires separate review for each file.",
            }

    def prepare(self, actor: str, name: str, fingerprint: str) -> dict[str, Any]:
        with self.manager.lock, self.manager.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            records = self._records(db)
            record = next((r for r in records if r["name"] == name and r["eligible"]), None)
            if record is None or self.fingerprint(records, self.days) != fingerprint:
                raise ValueError("Recovery selection changed or is protected")
            self.tokens = {k: v for k, v in self.tokens.items() if v["deadline"] > time.monotonic()}
            if len(self.tokens) >= 32:
                raise ValueError("Too many expiry approvals")
            token = secrets.token_urlsafe(32)
            self.tokens[token] = {
                "actor": actor,
                "record": record,
                "days": self.days,
                "fingerprint": fingerprint,
                "deadline": time.monotonic() + 300,
            }
            return {
                "token": token,
                "confirmation": "EXPIRE " + name,
                "file": record,
                "expires_in_seconds": 300,
            }

    def apply(self, actor: str, token: str, confirmation: str) -> dict[str, Any]:
        with self.manager.lock:
            approval = self.tokens.get(token)
            if (
                approval is None
                or approval["actor"] != actor
                or approval["deadline"] <= time.monotonic()
                or self.days != approval["days"]
                or confirmation != "EXPIRE " + approval["record"]["name"]
            ):
                raise ValueError("Expiry approval invalid or expired")
            record = approval["record"]
            # The durable intent survives a crash between file removal and final audit commit.
            request_id = Repository(self.manager.store).audit(
                None,
                "recovery.expiry.requested",
                {
                    "actor": actor,
                    "directory_id": self.directory_id,
                    "name": record["name"],
                    "sha256": record["sha256"],
                    "retention_days": self.days,
                },
            )
            removed = False
            try:
                with self.manager.store.connection() as db:
                    db.execute("BEGIN IMMEDIATE")
                    if self.fingerprint(self._records(db), self.days) != approval["fingerprint"]:
                        raise ValueError("Recovery files or protections changed; review again")
                    path = self.path(record["name"])
                    path.unlink()
                    removed = True
                    Repository(TransactionStore(db)).audit(
                        None,
                        "recovery.expiry.completed",
                        {
                            "actor": actor,
                            "request_id": request_id,
                            "outcome": "file_removed",
                        },
                    )
            except Exception:
                if not removed:
                    raise
                # No false claim of rollback: filesystem deletion cannot be rolled back by SQL.
                return {"status": "file_removed_audit_pending", "name": record["name"]}
            finally:
                self.tokens.pop(token, None)
            return {"status": "file_removed", "name": record["name"]}
