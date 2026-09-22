"""Pure, offline policy decisions for declared fixture targets."""

import hashlib
from datetime import UTC, datetime
from enum import IntEnum
from typing import Literal, cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aegis.modes import ModeId, capabilities


class Risk(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class Workspace(Model):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=100)
    mode: ModeId = "appsec"


class Scope(Model):
    workspace_id: UUID
    allowed_targets: tuple[str, ...]
    excluded_targets: tuple[str, ...] = ()
    capabilities: tuple[Literal["fixture.evaluate", "evidence.import"], ...]
    risk_ceiling: Risk = Risk.LOW
    starts_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def valid_window(self) -> "Scope":
        if self.starts_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("Timezone is required")
        if self.starts_at >= self.expires_at:
            raise ValueError("Invalid time window")
        for target in (*self.allowed_targets, *self.excluded_targets):
            normalize_target(target)
        return self


class Decision(Model):
    id: UUID = Field(default_factory=uuid4)
    allowed: bool
    reason: str
    workspace_id: UUID
    timestamp: datetime
    target: str | None
    capability: Literal["fixture.evaluate", "evidence.import"] | None
    scope_hash: str | None


def normalize_target(value: str) -> str:
    """Only exact fixture identifiers; intentionally not a network scope matcher."""
    if not value or len(value) > 200 or any(c.isspace() for c in value):
        raise ValueError("Invalid fixture target")
    parts = urlsplit(value)
    if (
        parts.scheme != "fixture"
        or not parts.netloc
        or parts.query
        or parts.fragment
        or not value.startswith("fixture://")
    ):
        raise ValueError("Expected fixture:// identifier")
    # Reject every ambiguous delimiter after the fixed scheme.
    suffix = value[len("fixture://") :]
    if any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_/." for c in suffix):
        raise ValueError("Invalid fixture identifier")
    if any(segment in ("", ".", "..") for segment in suffix.split("/")):
        raise ValueError("Ambiguous fixture identifier")
    return value


def authorize(
    workspace: Workspace,
    scope: Scope | None,
    target: str,
    capability: str,
    risk: Risk = Risk.LOW,
    *,
    now: datetime | None = None,
) -> Decision:
    timestamp = now or datetime.now(UTC)
    try:
        normalized = normalize_target(target)
    except ValueError:
        normalized = None

    def result(allowed: bool, reason: str) -> Decision:
        return Decision(
            allowed=allowed,
            reason=reason,
            workspace_id=workspace.id,
            timestamp=timestamp,
            target=normalized,
            capability=cast(Literal["fixture.evaluate", "evidence.import"], capability)
            if capability in ("fixture.evaluate", "evidence.import")
            else None,
            scope_hash=hashlib.sha256(scope.model_dump_json().encode()).hexdigest()
            if scope
            else None,
        )

    if timestamp.tzinfo is None:
        return result(False, "invalid_clock")
    if scope is None:
        return result(False, "missing_scope")
    if scope.workspace_id != workspace.id:
        return result(False, "workspace_mismatch")
    try:
        target = normalize_target(target)
    except ValueError:
        return result(False, "invalid_target")
    if not scope.starts_at <= timestamp < scope.expires_at:
        return result(False, "outside_time_window")
    if target in scope.excluded_targets:
        return result(False, "explicit_exclusion")
    if target not in scope.allowed_targets:
        return result(False, "unknown_target")
    if capability not in capabilities(workspace.mode) or capability not in scope.capabilities:
        return result(False, "capability_denied")
    if not isinstance(risk, Risk) or risk > min(scope.risk_ceiling, Risk.LOW):
        return result(False, "risk_denied")
    return result(True, "explicit_allow")
