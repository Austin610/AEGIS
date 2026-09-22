from uuid import uuid4

import pytest

from aegis.assurance import Fixture, evaluate
from aegis.gate import GatePolicy, evaluate_gate


@pytest.mark.parametrize(
    "before,after,severity,strict,expected",
    [
        ("deny", "deny", "high", False, 0),
        ("deny", "allow", "high", False, 1),
        ("deny", "allow", "low", False, 0),
        ("allow", "allow", "high", False, 0),
        ("allow", "allow", "low", True, 1),
        ("allow", "deny", "high", False, 0),
        ("deny", "unknown", "high", False, 2),
        ("error", "deny", "high", False, 2),
    ],
)
def test_gate_outcomes(before: str, after: str, severity: str, strict: bool, expected: int) -> None:
    workspace_id = uuid4()

    def run(observed: str):
        fixture = Fixture.model_validate(
            {
                "target": "fixture://local/test",
                "target_version": "v1",
                "policy_version": "p1",
                "identity_set": "test",
                "checks": [
                    {
                        "id": "test",
                        "title": "Test",
                        "expected": "deny",
                        "observed": observed,
                        "severity": severity,
                    }
                ],
            }
        )
        return evaluate(fixture, workspace_id, uuid4(), uuid4())

    result = evaluate_gate(run(before), run(after), GatePolicy(fail_on_any_violation=strict))
    assert result.exit_code == expected
