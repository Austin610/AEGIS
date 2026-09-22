"""Deterministic CI decisions over compatible, recorded observations."""

from typing import Literal

from aegis.assurance import Run, compare
from aegis.policy import Model

Severity = Literal["low", "medium", "high", "critical"]


class GatePolicy(Model):
    minimum_severity: Severity = "high"
    fail_on_any_violation: bool = False


class GateResult(Model):
    status: Literal["pass", "fail", "uncertain"]
    exit_code: Literal[0, 1, 2]
    reason: str
    blocking_checks: tuple[str, ...] = ()
    classifications: dict[str, str]


def evaluate_gate(previous: Run, current: Run, policy: GatePolicy | None = None) -> GateResult:
    policy = policy or GatePolicy()
    classifications = compare(previous, current)
    if any(value in {"uncertain", "newly_observed_failure"} for value in classifications.values()):
        return GateResult(
            status="uncertain",
            exit_code=2,
            reason="Incomplete or erroneous observations require review",
            classifications=classifications,
        )
    severity = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    blocking = []
    for assertion in current.assertions:
        if assertion.severity not in severity:
            raise ValueError("Unknown assertion severity")
        if assertion.outcome != "FAIL":
            continue
        if policy.fail_on_any_violation or (
            classifications[assertion.check_id] == "new_regression"
            and severity[assertion.severity] >= severity[policy.minimum_severity]
        ):
            blocking.append(assertion.check_id)
    return GateResult(
        status="fail" if blocking else "pass",
        exit_code=1 if blocking else 0,
        reason="Blocking assertion regressions" if blocking else "No blocking regression",
        blocking_checks=tuple(blocking),
        classifications=classifications,
    )
