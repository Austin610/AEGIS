"""Optional local parsers behind core interfaces; no target execution."""

import base64
import binascii
import hashlib
import json
from typing import Literal

from pydantic import Field, StrictBool, model_validator

from aegis.adapter_contract import AdapterOutput, Registry
from aegis.assurance import Fixture
from aegis.junit import parse_junit
from aegis.policy import Model
from aegis.sarif import SarifAdapter
from aegis.service import redact


class BaseAdapter:
    version = "1.0"
    capability: Literal["fixture.evaluate", "evidence.import"] = "evidence.import"

    async def healthcheck(self) -> bool:
        return True


class NoteInput(Model):
    title: str = Field(min_length=1, max_length=200)
    note: str = Field(min_length=1, max_length=20000)


class NotesAdapter(BaseAdapter):
    id, name = "notes", "Analyst notebook"
    modes: tuple[str, ...] = (
        "appsec",
        "qa",
        "api_security",
        "research",
        "forensics",
        "reverse",
        "ctf",
        "bug_bounty",
        "training",
    )

    async def execute(self, target: str, payload: dict[str, object]) -> AdapterOutput:
        note = NoteInput.model_validate(payload)
        return AdapterOutput(
            kind="analyst_note",
            data={
                "title": redact(note.title),
                "note": redact(note.note),
                "target": target,
                "verification": "manual observation; not independently verified",
            },
        )


class OpenAPIInput(Model):
    document: dict[str, object]


class OpenAPIAdapter(BaseAdapter):
    id, name = "openapi", "OpenAPI inventory"
    modes: tuple[str, ...] = ("appsec", "api_security", "qa", "research")

    async def execute(self, target: str, payload: dict[str, object]) -> AdapterOutput:
        document = OpenAPIInput.model_validate(payload).document
        version = document.get("openapi")
        if not isinstance(version, str) or not version.startswith("3."):
            raise ValueError("Expected OpenAPI 3.x document")
        paths = document.get("paths", {})
        if not isinstance(paths, dict) or len(paths) > 1000:
            raise ValueError("Invalid or oversized paths map")
        operations = []
        for path, item in paths.items():
            if not isinstance(path, str) or not path.startswith("/") or not isinstance(item, dict):
                raise ValueError("Invalid OpenAPI path")
            for method in ("get", "put", "post", "delete", "options", "head", "patch", "trace"):
                if method not in item:
                    continue
                operation = item[method]
                if not isinstance(operation, dict):
                    raise ValueError("Invalid OpenAPI operation")
                security = operation.get("security", document.get("security"))
                status = (
                    "unspecified"
                    if security is None
                    else "declared"
                    if security
                    else "not_required"
                )
                operations.append(
                    {"method": method.upper(), "path": redact(path[:500]), "security": status}
                )
        return AdapterOutput(
            kind="openapi_inventory",
            data={
                "version": version,
                "target": target,
                "operations": operations,
                "operation_count": len(operations),
                "remote_references_resolved": False,
                "interpretation": "Specification metadata only; not a vulnerability assessment",
            },
        )


class FileInput(Model):
    name: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(max_length=700000)


class FileMetadataAdapter(BaseAdapter):
    id, name = "file-metadata", "File identification and hashing"
    modes: tuple[str, ...] = ("forensics", "reverse", "ctf", "research")

    async def execute(self, target: str, payload: dict[str, object]) -> AdapterOutput:
        source = FileInput.model_validate(payload)
        try:
            content = base64.b64decode(source.content_base64, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("Invalid file encoding") from None
        if len(content) > 524288:
            raise ValueError("Files must be at most 512 KiB")
        kind = "unknown"
        for magic, label in (
            (b"\x7fELF", "ELF"),
            (b"MZ", "PE/DOS"),
            (b"%PDF-", "PDF"),
            (b"PK\x03\x04", "ZIP"),
            (b"\x89PNG\r\n\x1a\n", "PNG"),
        ):
            if content.startswith(magic):
                kind = label
                break
        return AdapterOutput(
            kind="file_metadata",
            data={
                "name": redact(source.name.replace("\\", "/").split("/")[-1]),
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "format_hint": kind,
                "target": target,
                "executed": False,
                "original_retained": False,
            },
        )


class Sample(Model):
    truth: StrictBool
    predicted: StrictBool | None


class ExperimentInput(Model):
    name: str = Field(min_length=1, max_length=100)
    samples: tuple[Sample, ...] = Field(min_length=1, max_length=10000)


class ResearchAdapter(BaseAdapter):
    id, name = "evaluation", "Labeled-result metrics"
    modes: tuple[str, ...] = ("research",)

    async def execute(self, target: str, payload: dict[str, object]) -> AdapterOutput:
        data = ExperimentInput.model_validate(payload)
        tp = sum(s.truth and s.predicted is True for s in data.samples)
        fp = sum(not s.truth and s.predicted is True for s in data.samples)
        fn = sum(s.truth and s.predicted is False for s in data.samples)
        tn = sum(not s.truth and s.predicted is False for s in data.samples)
        unknown = sum(s.predicted is None for s in data.samples)
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
        return AdapterOutput(
            kind="experiment_metrics",
            data={
                "name": redact(data.name),
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn,
                "unknown": unknown,
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "coverage": (len(data.samples) - unknown) / len(data.samples),
                "dataset_hash": hashlib.sha256(data.model_dump_json().encode()).hexdigest(),
                "sample_count": len(data.samples),
                "target": target,
                "interpretation": (
                    "Unknown predictions excluded from precision/recall; "
                    "coverage reported separately"
                ),
            },
        )


class JUnitInput(Model):
    xml: str = Field(max_length=1_048_576)
    revision: str = Field(min_length=1, max_length=100)
    suite_version: str = Field(default="v1", min_length=1, max_length=100)


class JUnitAdapter(BaseAdapter):
    id, name = "junit", "JUnit result import"
    modes: tuple[str, ...] = ("qa",)
    capability: Literal["fixture.evaluate", "evidence.import"] = "fixture.evaluate"

    async def execute(self, target: str, payload: dict[str, object]) -> AdapterOutput:
        data = JUnitInput.model_validate(payload)
        return AdapterOutput(
            kind="junit_observation",
            fixture=parse_junit(
                data.xml,
                target,
                data.revision,
                data.suite_version,
            ),
        )


class FixtureAdapter(BaseAdapter):
    id, name = "fixture", "Declared invariant observations"
    modes: tuple[str, ...] = ("appsec", "qa", "api_security", "research", "training")
    capability: Literal["fixture.evaluate", "evidence.import"] = "fixture.evaluate"

    async def execute(self, target: str, payload: dict[str, object]) -> AdapterOutput:
        fixture = Fixture.model_validate(payload)
        if target != fixture.target:
            raise ValueError("Fixture target must match requested target")
        return AdapterOutput(kind="fixture_observation", fixture=fixture)


class ThresholdInput(Model):
    revision: str = Field(min_length=1, max_length=100)
    metrics: dict[str, float] = Field(min_length=1, max_length=100)
    maximums: dict[str, float] = Field(default_factory=dict, max_length=100)
    minimums: dict[str, float] = Field(default_factory=dict, max_length=100)

    @model_validator(mode="after")
    def valid_bounds(self) -> "ThresholdInput":
        if not self.minimums and not self.maximums:
            raise ValueError("At least one threshold is required")
        for name in self.minimums.keys() & self.maximums.keys():
            if self.minimums[name] > self.maximums[name]:
                raise ValueError("Minimum exceeds maximum")
        return self


class ThresholdAdapter(BaseAdapter):
    id, name = "thresholds", "Performance threshold results"
    modes: tuple[str, ...] = ("qa", "research")
    capability: Literal["fixture.evaluate", "evidence.import"] = "fixture.evaluate"

    async def execute(self, target: str, payload: dict[str, object]) -> AdapterOutput:
        import math

        values = ThresholdInput.model_validate(payload)
        if any(
            not math.isfinite(v)
            for v in (
                *values.metrics.values(),
                *values.maximums.values(),
                *values.minimums.values(),
            )
        ):
            raise ValueError("Metrics must be finite numbers")
        checks = []
        for name, limit in values.maximums.items():
            observation = values.metrics.get(name)
            checks.append(
                {
                    "id": "metric-" + hashlib.sha256(name.encode()).hexdigest()[:16],
                    "title": redact(name[:160]) + " within declared maximum",
                    "expected": "allow",
                    "observed": "unknown"
                    if observation is None
                    else "allow"
                    if observation <= limit
                    else "deny",
                }
            )
        for name, limit in values.minimums.items():
            observation = values.metrics.get(name)
            checks.append(
                {
                    "id": "minimum-" + hashlib.sha256(name.encode()).hexdigest()[:16],
                    "title": redact(name[:160]) + " meets declared minimum",
                    "expected": "allow",
                    "observed": "unknown"
                    if observation is None
                    else "allow"
                    if observation >= limit
                    else "deny",
                }
            )
        policy = (
            values.maximums
            if not values.minimums
            else {
                "minimums": values.minimums,
                "maximums": values.maximums,
            }
        )
        return AdapterOutput(
            kind="fixture_observation",
            data={
                "measurements": [
                    {"name": name, "value": value} for name, value in values.metrics.items()
                ],
                "bounds": [
                    {
                        "name": name,
                        "minimum": values.minimums.get(name),
                        "maximum": values.maximums.get(name),
                    }
                    for name in sorted(values.minimums.keys() | values.maximums.keys())
                ],
            },
            fixture=Fixture.model_validate(
                {
                    "target": target,
                    "target_version": values.revision,
                    "policy_version": hashlib.sha256(
                        json.dumps(policy, sort_keys=True).encode()
                    ).hexdigest(),
                    "identity_set": "performance",
                    "checks": checks,
                }
            ),
        )


def default_registry() -> Registry:
    from aegis.invariants import InvariantAdapter

    registry = Registry()
    for adapter in (
        NotesAdapter(),
        SarifAdapter(),
        OpenAPIAdapter(),
        FileMetadataAdapter(),
        ResearchAdapter(),
        JUnitAdapter(),
        FixtureAdapter(),
        ThresholdAdapter(),
        InvariantAdapter(),
    ):
        registry.register(adapter)
    return registry
