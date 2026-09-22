"""Bounded offline JUnit import. Failure bodies and properties are not retained."""

import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

from aegis.assurance import Fixture


def load_junit(path: Path, target: str, revision: str, suite_version: str) -> Fixture:
    if path.stat().st_size > 1_048_576:
        raise ValueError("JUnit input exceeds 1 MiB limit")
    text = path.read_text(encoding="utf-8")
    return parse_junit(text, target, revision, suite_version)


def parse_junit(text: str, target: str, revision: str, suite_version: str) -> Fixture:
    if len(text.encode("utf-8")) > 1_048_576:
        raise ValueError("JUnit input exceeds 1 MiB limit")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("DTD and entity declarations are not supported")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise ValueError("Invalid JUnit document") from None
    if root.tag not in ("testsuite", "testsuites"):
        raise ValueError("Expected JUnit testsuite or testsuites root")
    checks: list[dict[str, str]] = []
    for case in root.iter("testcase"):
        name = case.get("name", "")
        classname = case.get("classname", "")
        if not name or len(checks) >= 1000:
            raise ValueError("JUnit requires named tests and at most 1000 cases")
        outcomes = [tag for tag in ("error", "failure", "skipped") if case.find(tag) is not None]
        if len(outcomes) > 1:
            raise ValueError("Contradictory JUnit testcase outcomes")
        observed = {"error": "error", "failure": "deny", "skipped": "unknown"}.get(
            outcomes[0] if outcomes else "", "allow"
        )
        identity = hashlib.sha256(f"{classname}\0{name}".encode()).hexdigest()[:32]
        checks.append(
            {
                "id": f"junit-{identity}",
                "title": f"{classname}: {name}"[:200],
                "expected": "allow",
                "observed": observed,
                "severity": "medium",
            }
        )
    return Fixture.model_validate(
        {
            "target": target,
            "target_version": revision,
            "policy_version": suite_version,
            "identity_set": "junit-suite",
            "checks": checks,
        }
    )
