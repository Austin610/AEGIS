"""Declarative checks over supplied JSON observations; no requests or executable expressions."""

import json
from typing import Any, Literal

from pydantic import Field, StrictInt, StrictStr

from aegis.adapter_contract import AdapterOutput
from aegis.assurance import Check, Fixture, digest
from aegis.policy import Model


class Invariant(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$")
    title: str = Field(min_length=1, max_length=200)
    path: list[StrictStr | StrictInt] = Field(min_length=1, max_length=20)
    operator: Literal[
        "equals", "not_equals", "exists", "less_than", "at_most", "at_least", "contains"
    ]
    expected: Any = None
    severity: Literal["low", "medium", "high", "critical"] = "medium"


class InvariantInput(Model):
    revision: str = Field(min_length=1, max_length=100)
    observations: dict[str, Any]
    checks: list[Invariant] = Field(min_length=1, max_length=1000)


def observation(values: dict[str, Any], path: list[str | int]) -> tuple[bool, Any]:
    current: Any = values
    for key in path:
        if isinstance(current, dict) and isinstance(key, str) and key in current:
            current = current[key]
        elif isinstance(current, list) and type(key) is int and 0 <= key < len(current):
            current = current[key]
        else:
            return False, None
    return True, current


def outcome(check: Invariant, present: bool, value: Any) -> str:
    if check.operator == "exists":
        if type(check.expected) is not bool:
            raise ValueError("Exists requires a boolean expectation")
        return "allow" if present == check.expected else "deny"
    if not present:
        return "unknown"
    expected = check.expected
    if check.operator in {"less_than", "at_most", "at_least"}:
        if type(value) not in {int, float} or type(expected) not in {int, float}:
            return "error"
        passed = (
            value < expected
            if check.operator == "less_than"
            else value <= expected
            if check.operator == "at_most"
            else value >= expected
        )
    elif check.operator == "contains":
        if isinstance(value, str) and isinstance(expected, str):
            passed = expected in value
        elif isinstance(value, list):
            passed = any(digest(v) == digest(expected) for v in value)
        else:
            return "error"
    else:
        passed = digest(value) == digest(expected)
        if check.operator == "not_equals":
            passed = not passed
    return "allow" if passed else "deny"


class InvariantAdapter:
    id, name, version = "invariants", "Recorded JSON invariants", "1.0"
    modes: tuple[str, ...] = ("appsec", "qa", "api_security", "research", "training")
    capability: Literal["fixture.evaluate", "evidence.import"] = "fixture.evaluate"

    async def healthcheck(self) -> bool:
        return True

    async def execute(self, target: str, payload: dict[str, object]) -> AdapterOutput:
        if len(json.dumps(payload, allow_nan=False).encode()) > 1_048_576:
            raise ValueError("Invariant input exceeds 1 MiB")
        values = InvariantInput.model_validate(payload)
        checks = []
        for check in values.checks:
            present, value = observation(values.observations, check.path)
            checks.append(
                Check.model_validate(
                    {
                        "id": check.id,
                        "title": check.title,
                        "severity": check.severity,
                        "expected": "allow",
                        "observed": outcome(check, present, value),
                    }
                )
            )
        return AdapterOutput(
            kind="fixture_observation",
            data={
                "observations": values.observations,
                "definitions": [c.model_dump() for c in values.checks],
                "verification": "Checks against supplied JSON; no live behavior verified",
            },
            fixture=Fixture(
                target=target,
                target_version=values.revision,
                policy_version=digest(
                    sorted([c.model_dump() for c in values.checks], key=lambda c: c["id"])
                ),
                identity_set="recorded-json",
                checks=tuple(checks),
            ),
        )
