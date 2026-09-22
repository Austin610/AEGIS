"""Evaluate explicitly supplied fixture observations, never generate requests."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from aegis.policy import Model, normalize_target

Outcome = Literal["PASS", "FAIL", "UNKNOWN", "ERROR"]


class Check(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$")
    title: str = Field(min_length=1, max_length=200)
    expected: Literal["allow", "deny"]
    observed: Literal["allow", "deny", "unknown", "error"]
    severity: Literal["low", "medium", "high", "critical"] = "medium"


class Fixture(Model):
    target: str
    target_version: str = Field(min_length=1, max_length=100)
    policy_version: str = Field(min_length=1, max_length=100)
    identity_set: str = Field(min_length=1, max_length=100)
    checks: tuple[Check, ...] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_fixture(self) -> "Fixture":
        normalize_target(self.target)
        if len({check.id for check in self.checks}) != len(self.checks):
            raise ValueError("Duplicate check identifiers")
        return self


class Assertion(Model):
    check_id: str
    title: str
    outcome: Outcome
    severity: str
    expected: str
    observed: str


class Finding(Model):
    id: UUID = Field(default_factory=uuid4)
    fingerprint: str
    title: str
    severity: str
    check_id: str
    evidence_id: UUID
    confidence: Literal["fixture_observation"] = "fixture_observation"


class Run(Model):
    id: UUID = Field(default_factory=uuid4)
    workspace_id: UUID
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    target: str
    target_version: str
    compatibility_hash: str
    evidence_id: UUID
    decision_id: UUID
    assertions: tuple[Assertion, ...]
    findings: tuple[Finding, ...]


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def evaluate(fixture: Fixture, workspace_id: UUID, decision_id: UUID, evidence_id: UUID) -> Run:
    assertions = []
    findings = []
    for check in fixture.checks:
        outcome: Outcome
        if check.observed == "unknown":
            outcome = "UNKNOWN"
        elif check.observed == "error":
            outcome = "ERROR"
        else:
            outcome = "PASS" if check.expected == check.observed else "FAIL"
        assertions.append(
            Assertion(
                check_id=check.id,
                title=check.title,
                outcome=outcome,
                severity=check.severity,
                expected=check.expected,
                observed=check.observed,
            )
        )
        if outcome == "FAIL":
            findings.append(
                Finding(
                    fingerprint=digest([str(workspace_id), fixture.target, check.id]),
                    title=check.title,
                    severity=check.severity,
                    check_id=check.id,
                    evidence_id=evidence_id,
                )
            )
    compatibility = {
        "target": fixture.target,
        "policy_version": fixture.policy_version,
        "identity_set": fixture.identity_set,
        "checks": sorted((c.id, c.expected, c.severity) for c in fixture.checks),
    }
    return Run(
        workspace_id=workspace_id,
        target=fixture.target,
        target_version=fixture.target_version,
        compatibility_hash=digest(compatibility),
        evidence_id=evidence_id,
        decision_id=decision_id,
        assertions=tuple(assertions),
        findings=tuple(findings),
    )


def compare(previous: Run, current: Run) -> dict[str, str]:
    if (
        previous.workspace_id != current.workspace_id
        or previous.compatibility_hash != current.compatibility_hash
    ):
        raise ValueError("Incompatible workspace, target, policy, checks or identities")
    before = {a.check_id: a.outcome for a in previous.assertions}
    classifications = {
        ("PASS", "PASS"): "unchanged_pass",
        ("PASS", "FAIL"): "new_regression",
        ("FAIL", "FAIL"): "existing_failure",
        ("FAIL", "PASS"): "fixed",
        ("UNKNOWN", "FAIL"): "newly_observed_failure",
    }
    return {
        assertion.check_id: classifications.get(
            (before[assertion.check_id], assertion.outcome), "uncertain"
        )
        for assertion in current.assertions
    }
