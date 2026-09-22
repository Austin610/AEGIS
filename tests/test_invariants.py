import asyncio

import pytest

from aegis.invariants import InvariantAdapter


def test_recorded_invariant_operators_and_policy_compatibility():
    adapter = InvariantAdapter()
    definitions = [
        {"id": "eq", "title": "Equal", "path": ["status"], "operator": "equals", "expected": 403},
        {
            "id": "missing",
            "title": "Missing",
            "path": ["missing"],
            "operator": "equals",
            "expected": 1,
        },
        {
            "id": "absent",
            "title": "Absent",
            "path": ["missing"],
            "operator": "exists",
            "expected": False,
        },
        {"id": "type", "title": "Type", "path": ["flag"], "operator": "at_most", "expected": 1},
    ]
    payload = {
        "revision": "1",
        "observations": {"status": 403, "flag": True},
        "checks": definitions,
    }
    result = asyncio.run(adapter.execute("fixture://local/sample", payload))
    assert [c.observed for c in result.fixture.checks] == ["allow", "unknown", "allow", "error"]
    definitions[0]["expected"] = 200
    changed = asyncio.run(adapter.execute("fixture://local/sample", payload))
    assert result.fixture.policy_version != changed.fixture.policy_version
    assert changed.fixture.checks[0].observed == "deny"
    payload["observations"]["number"] = float("nan")
    with pytest.raises(ValueError):
        asyncio.run(adapter.execute("fixture://local/sample", payload))
