import asyncio
import copy
import socket

import pytest

from aegis.sarif import SarifAdapter


def document() -> dict:
    return {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Example analyzer",
                        "rules": [{"id": "rule-1", "defaultConfiguration": {"level": "error"}}],
                    }
                },
                "artifacts": [{"location": {"uri": "src/example.py"}}],
                "results": [
                    {
                        "ruleIndex": 0,
                        "message": {"text": "Review this observation"},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"index": 0},
                                    "region": {"startLine": 12},
                                }
                            }
                        ],
                    }
                ],
            }
        ],
    }


def parse(source: dict):
    return asyncio.run(SarifAdapter().execute("fixture://local/sample", {"document": source}))


def test_sarif_levels_suppressions_absence_and_provenance() -> None:
    source = document()
    first = source["runs"][0]["results"][0]
    suppressed = {
        **copy.deepcopy(first),
        "suppressions": [{"kind": "external", "status": "accepted"}],
    }
    absent = {**copy.deepcopy(first), "baselineState": "absent"}
    informational = {**copy.deepcopy(first), "kind": "informational"}
    review = {
        **copy.deepcopy(first),
        "suppressions": [{"kind": "external", "status": "underReview"}],
    }
    source["runs"][0]["results"] += [suppressed, absent, informational, review]
    result = parse(source)
    assert result.kind == "sarif_report"
    assert result.fixture is None
    assert result.data["result_count"] == 5
    assert result.data["active_failures_by_level"] == {
        "error": 2,
        "warning": 0,
        "note": 0,
        "none": 0,
    }
    assert result.data["results"][0]["locations"] == [
        {"uri": "src/example.py", "base_id": "", "line": 12}
    ]
    assert result.data["results"][3]["level"] == "none"
    assert result.data["source_sha256"] == parse(source).data["source_sha256"]
    source["runs"][0]["results"][0]["message"]["text"] = "Different observation"
    assert result.data["source_sha256"] != parse(source).data["source_sha256"]


def test_sarif_never_resolves_references_and_redacts(monkeypatch) -> None:
    def denied(*args, **kwargs):
        raise AssertionError("Network access attempted")

    monkeypatch.setattr(socket, "create_connection", denied)
    source = document()
    run = source["runs"][0]
    run["externalPropertyFileReferences"] = {
        "results": [{"location": {"uri": "https://example.invalid/results"}}]
    }
    run["invocations"] = [{"executionSuccessful": False}]
    run["results"][0]["message"] = {"text": "token=secret-canary"}
    result = parse(source)
    assert "secret-canary" not in result.model_dump_json()
    assert result.data["references_resolved"] is False
    assert result.data["external_references_present"] is True
    assert result.data["tools"][0]["execution"] == "failed"


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(version="2.0"),
        lambda d: d["runs"][0]["results"][0].update(ruleIndex=5),
        lambda d: d["runs"][0]["results"][0].update(ruleId="mismatched"),
        lambda d: d["runs"][0]["results"][0].update(level="critical"),
        lambda d: d["runs"][0]["results"][0].update(kind="pass", level="error"),
        lambda d: d["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["region"].update(
            startLine=True
        ),
        lambda d: d.update(properties={"oversized": "x" * 1_048_576}),
        lambda d: d.update(properties={"number": float("nan")}),
    ],
)
def test_sarif_rejects_invalid_or_oversized_input(change) -> None:
    source = document()
    change(source)
    with pytest.raises(ValueError):
        parse(source)


def test_missing_results_and_unresolved_messages_are_not_security_passes() -> None:
    result = parse({"version": "2.1.0", "runs": None})
    assert result.data["result_count"] == 0
    assert "do not establish security" in result.data["verification"]
    source = document()
    source["runs"][0]["results"][0]["message"] = {"id": "external-template"}
    result = parse(source)
    assert result.data["results"][0]["message_resolved"] is False
