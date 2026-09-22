"""Mode catalog for local assurance and offline analysis."""

from typing import Literal

ModeId = Literal[
    "appsec",
    "qa",
    "api_security",
    "research",
    "forensics",
    "reverse",
    "ctf",
    "bug_bounty",
    "training",
]

MODES = {
    "appsec": {"name": "AppSec", "description": "Invariants and security regression evidence"},
    "qa": {"name": "Quality assurance", "description": "JUnit results and threshold assertions"},
    "api_security": {"name": "API security", "description": "Offline OpenAPI inventory and policy"},
    "research": {
        "name": "Research",
        "description": "Labeled-result evaluation and reproducibility",
    },
    "forensics": {"name": "Forensics", "description": "File hashes and bounded metadata"},
    "reverse": {"name": "Reverse engineering", "description": "Offline binary metadata and notes"},
    "ctf": {"name": "CTF notebook", "description": "Challenge artifacts and analyst notes"},
    "bug_bounty": {
        "name": "Disclosure notebook",
        "description": "Manual observations and report drafts",
    },
    "training": {"name": "Training", "description": "Synthetic assurance exercises"},
}

GUIDES = {
    "appsec": "Import SARIF/checks → triage → link retests → review coverage and reports.",
    "qa": "Import JUnit → set thresholds → save a baseline → compare builds and CI gates.",
    "api_security": "Import OpenAPI → record observations → check invariants → review gaps.",
    "research": "Import labels → inspect metrics and unknowns → attach methodology notes.",
    "forensics": "Import file bytes → inspect hashes → record provenance → export evidence.",
    "reverse": "Import binary bytes → inspect static metadata → record notes. No execution.",
    "ctf": "Record supplied challenge artifacts, tasks and notes. No automated exploitation.",
    "bug_bounty": "Import observations → triage → draft a report. Nothing is submitted.",
    "training": "Use synthetic observations → run checks → review retests and lessons.",
}
for mode, guide in GUIDES.items():
    MODES[mode]["workflow_guide"] = guide


def capabilities(mode: str) -> set[str]:
    if mode in {"appsec", "qa", "api_security", "research", "training"}:
        return {"fixture.evaluate", "evidence.import"}
    if mode in MODES:
        return {"evidence.import"}
    return set()
