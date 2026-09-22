"""Bounded SARIF 2.1.0 result import; never resolves files, URLs or executable fixes."""

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from aegis.adapter_contract import AdapterOutput
from aegis.policy import Model
from aegis.service import redact


class SarifModel(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)


class Message(SarifModel):
    text: str | None = Field(default=None, max_length=20000)
    markdown: str | None = Field(default=None, max_length=20000)
    id: str | None = Field(default=None, max_length=500)


class Configuration(SarifModel):
    level: Literal["error", "warning", "note", "none"] = "warning"


class Rule(SarifModel):
    id: str = Field(min_length=1, max_length=500)
    defaultConfiguration: Configuration = Field(default_factory=Configuration)


class Driver(SarifModel):
    name: str = Field(min_length=1, max_length=200)
    version: str | None = Field(default=None, max_length=200)
    rules: list[Rule] = Field(default_factory=list, max_length=10000)


class Tool(SarifModel):
    driver: Driver


class ArtifactLocation(SarifModel):
    uri: str | None = Field(default=None, max_length=4000)
    uriBaseId: str | None = Field(default=None, max_length=200)
    index: StrictInt = Field(default=-1, ge=-1)


class Artifact(SarifModel):
    location: ArtifactLocation = Field(default_factory=ArtifactLocation)


class Region(SarifModel):
    startLine: StrictInt | None = Field(default=None, ge=1)


class PhysicalLocation(SarifModel):
    artifactLocation: ArtifactLocation = Field(default_factory=ArtifactLocation)
    region: Region = Field(default_factory=Region)


class Location(SarifModel):
    physicalLocation: PhysicalLocation = Field(default_factory=PhysicalLocation)


class Suppression(SarifModel):
    kind: Literal["inSource", "external"]
    status: Literal["accepted", "underReview", "rejected"] | None = None


class Result(SarifModel):
    ruleId: str | None = Field(default=None, max_length=500)
    ruleIndex: StrictInt = Field(default=-1, ge=-1)
    level: Literal["error", "warning", "note", "none"] | None = None
    kind: Literal["notApplicable", "pass", "fail", "review", "open", "informational"] = "fail"
    message: Message
    locations: list[Location] = Field(default_factory=list, max_length=100)
    suppressions: list[Suppression] | None = Field(default=None, max_length=100)
    baselineState: Literal["new", "unchanged", "updated", "absent"] | None = None


class Invocation(SarifModel):
    executionSuccessful: bool


class SarifRun(SarifModel):
    tool: Tool
    results: list[Result] = Field(default_factory=list, max_length=10000)
    artifacts: list[Artifact] = Field(default_factory=list, max_length=10000)
    invocations: list[Invocation] = Field(default_factory=list, max_length=100)
    externalPropertyFileReferences: dict[str, Any] | None = None


class Document(SarifModel):
    version: Literal["2.1.0"]
    runs: list[SarifRun] | None = Field(max_length=100)


class SarifInput(Model):
    document: dict[str, object]


class SarifAdapter:
    id, name, version = "sarif", "SARIF analysis results", "1.0"
    modes: tuple[str, ...] = ("appsec", "qa", "api_security", "research", "bug_bounty", "training")
    capability: Literal["fixture.evaluate", "evidence.import"] = "evidence.import"

    async def healthcheck(self) -> bool:
        return True

    async def execute(self, target: str, payload: dict[str, object]) -> AdapterOutput:
        source = SarifInput.model_validate(payload).document
        encoded = json.dumps(
            source, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        if len(encoded) > 1_048_576:
            raise ValueError("SARIF document exceeds 1 MiB")
        document = Document.model_validate(source)
        results: list[dict[str, object]] = []
        tools: list[dict[str, object]] = []
        counts = {"error": 0, "warning": 0, "note": 0, "none": 0}
        unresolved = False
        for run_index, run in enumerate(document.runs or []):
            driver = run.tool.driver
            rules = {rule.id: rule for rule in driver.rules}
            if len(rules) != len(driver.rules):
                raise ValueError("Duplicate SARIF rule identifiers")
            tools.append(
                {
                    "name": redact(driver.name),
                    "version": redact(driver.version or "unknown"),
                    "execution": "failed"
                    if any(not i.executionSuccessful for i in run.invocations)
                    else "reported_success"
                    if run.invocations
                    else "unknown",
                }
            )
            unresolved |= bool(run.externalPropertyFileReferences)
            for result in run.results:
                if len(results) >= 10000:
                    raise ValueError("SARIF contains more than 10000 results")
                rule = rules.get(result.ruleId or "")
                if result.ruleIndex >= 0:
                    if result.ruleIndex >= len(driver.rules):
                        raise ValueError("Invalid SARIF rule index")
                    indexed = driver.rules[result.ruleIndex]
                    if result.ruleId is not None and indexed.id != result.ruleId:
                        raise ValueError("SARIF rule id/index mismatch")
                    rule = indexed
                rule_id = redact(result.ruleId or (rule.id if rule else "unspecified"))
                level = result.level or (rule.defaultConfiguration.level if rule else "warning")
                if result.kind != "fail":
                    if result.level not in (None, "none"):
                        raise ValueError("Non-failing SARIF results must use level none")
                    level = "none"
                suppressed = any(s.status == "accepted" for s in result.suppressions or [])
                state = (
                    "absent"
                    if result.baselineState == "absent"
                    else "suppressed"
                    if suppressed
                    else result.kind
                )
                if state == "fail":
                    counts[level] += 1
                message = result.message.text or result.message.markdown
                resolved_message = message is not None
                message = redact(
                    message
                    or "Unresolved message template: " + (result.message.id or "unspecified")
                )
                locations: list[dict[str, object]] = []
                for location in result.locations:
                    physical = location.physicalLocation
                    artifact = physical.artifactLocation
                    if artifact.index >= 0 and artifact.uri is None:
                        if artifact.index >= len(run.artifacts):
                            raise ValueError("Invalid SARIF artifact index")
                        artifact = run.artifacts[artifact.index].location
                    locations.append(
                        {
                            "uri": redact(artifact.uri or "unknown"),
                            "base_id": redact(artifact.uriBaseId or ""),
                            "line": physical.region.startLine,
                        }
                    )
                results.append(
                    {
                        "tool": redact(driver.name),
                        "run_index": run_index,
                        "rule_id": rule_id,
                        "level": level,
                        "kind": result.kind,
                        "state": state,
                        "baseline_state": result.baselineState,
                        "message": message,
                        "message_resolved": resolved_message,
                        "locations": locations,
                        "suppression_states": [
                            s.status or "unknown" for s in result.suppressions or []
                        ],
                    }
                )
        return AdapterOutput(
            kind="sarif_report",
            data={
                "target": target,
                "source_format": "SARIF 2.1.0",
                "source_sha256": hashlib.sha256(encoded).hexdigest(),
                "tools": tools,
                "results": results,
                "result_count": len(results),
                "active_failures_by_level": counts,
                "external_references_present": unresolved,
                "references_resolved": False,
                "verification": (
                    "Imported tool observations; not independently verified. "
                    "Empty results do not establish security."
                ),
            },
        )
