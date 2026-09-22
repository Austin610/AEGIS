import asyncio
import base64

import pytest

from aegis.adapter_contract import Registry
from aegis.adapters import NotesAdapter, default_registry


@pytest.mark.parametrize(
    "adapter_id,payload,kind",
    [
        ("notes", {"title": "Observation", "note": "Review completed"}, "analyst_note"),
        (
            "openapi",
            {"document": {"openapi": "3.0.3", "paths": {"/items": {"get": {"security": []}}}}},
            "openapi_inventory",
        ),
        (
            "file-metadata",
            {"name": "sample.pdf", "content_base64": base64.b64encode(b"%PDF-sample").decode()},
            "file_metadata",
        ),
        (
            "evaluation",
            {
                "name": "evaluation",
                "samples": [
                    {"truth": True, "predicted": True},
                    {"truth": False, "predicted": None},
                ],
            },
            "experiment_metrics",
        ),
        (
            "junit",
            {"xml": '<testsuite><testcase name="x"/></testsuite>', "revision": "v1"},
            "junit_observation",
        ),
        (
            "fixture",
            {
                "target": "fixture://local/sample",
                "target_version": "v1",
                "policy_version": "p1",
                "identity_set": "test",
                "checks": [{"id": "x", "title": "Check", "expected": "allow", "observed": "allow"}],
            },
            "fixture_observation",
        ),
        (
            "thresholds",
            {"revision": "v1", "metrics": {"latency": 600}, "maximums": {"latency": 500}},
            "fixture_observation",
        ),
    ],
)
def test_adapter_contract(adapter_id: str, payload: dict, kind: str) -> None:
    async def scenario() -> None:
        adapter = default_registry().get(adapter_id)
        assert adapter.version
        assert adapter.modes
        assert await adapter.healthcheck()
        result = await adapter.execute("fixture://local/sample", payload)
        assert result.kind == kind
        if kind == "file_metadata":
            assert result.data["format_hint"] == "PDF"
            assert result.data["executed"] is False
        if kind == "experiment_metrics":
            assert result.data["precision"] == 1
            assert result.data["coverage"] == 0.5
            assert result.data["unknown"] == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "adapter_id,payload",
    [
        ("file-metadata", {"name": "x", "content_base64": "not base64"}),
        (
            "file-metadata",
            {"name": "x", "content_base64": base64.b64encode(b"x" * 524289).decode()},
        ),
        ("openapi", {"document": {"openapi": "2.0", "paths": {}}}),
        ("openapi", {"document": {"openapi": "3.0", "paths": {"bad": {}}}}),
        ("evaluation", {"name": "x", "samples": [{"truth": "true", "predicted": True}]}),
        ("thresholds", {"revision": "v1", "metrics": {"a": float("nan")}, "maximums": {"a": 1}}),
    ],
)
def test_invalid_adapter_inputs(adapter_id: str, payload: dict) -> None:
    with pytest.raises(ValueError):
        asyncio.run(default_registry().get(adapter_id).execute("fixture://local/sample", payload))


def test_registry_and_unknown_prediction() -> None:
    registry = Registry()
    registry.register(NotesAdapter())
    with pytest.raises(ValueError):
        registry.register(NotesAdapter())
    with pytest.raises(ValueError):
        registry.get("not-installed")
    result = asyncio.run(
        default_registry()
        .get("evaluation")
        .execute(
            "fixture://local/sample",
            {
                "name": "unknown",
                "samples": [{"truth": True, "predicted": None}],
            },
        )
    )
    assert result.data["precision"] is None
    assert result.data["recall"] is None
    assert result.data["coverage"] == 0


@pytest.mark.parametrize(
    "metrics,expected",
    [
        ({"coverage": 0.9, "latency": 300}, ["allow", "allow"]),
        ({"coverage": 0.5, "latency": 700}, ["deny", "deny"]),
        ({"unrelated": 1}, ["unknown", "unknown"]),
    ],
)
def test_minimum_and_maximum_thresholds(metrics: dict, expected: list) -> None:
    result = asyncio.run(
        default_registry()
        .get("thresholds")
        .execute(
            "fixture://local/sample",
            {
                "revision": "build-1",
                "metrics": metrics,
                "minimums": {"coverage": 0.8},
                "maximums": {"latency": 500},
            },
        )
    )
    assert [check.observed for check in result.fixture.checks] == expected


@pytest.mark.parametrize(
    "minimums,maximums", [({}, {}), ({"a": 10}, {"a": 5}), ({"a": float("inf")}, {})]
)
def test_invalid_threshold_bounds(minimums: dict, maximums: dict) -> None:
    with pytest.raises(ValueError):
        asyncio.run(
            default_registry()
            .get("thresholds")
            .execute(
                "fixture://local/sample",
                {
                    "revision": "build-1",
                    "metrics": {"a": 1},
                    "minimums": minimums,
                    "maximums": maximums,
                },
            )
        )
