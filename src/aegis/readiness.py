"""Release acceptance criteria; completion is never inferred from feature counts."""

CRITERIA = [
    (
        "foundation",
        "Features",
        "Local evidence, scopes, jobs and baselines",
        "verified",
        "Existing Python and browser acceptance tests; offline execution only.",
    ),
    (
        "storage",
        "Features",
        "Backup, restore and optional PostgreSQL",
        "verified",
        "SQLite snapshots and isolated PostgreSQL integration checks completed.",
    ),
    (
        "workflow",
        "Workflows",
        "Findings, triage, retest and stakeholder reports",
        "verified",
        "API checks cover source identity, permissions, revision conflicts and closure evidence.",
    ),
    (
        "coverage",
        "Workflows",
        "Requirements coverage and offline mode acceptance",
        "verified",
        "Coverage browser checks and one persisted offline result scenario per mode pass. "
        "This does not establish completion of every original blueprint milestone.",
    ),
    (
        "pagination",
        "Features",
        "Snapshot-consistent enumeration",
        "verified",
        "Freeze runs/jobs/artifacts/baselines/audit pages for five minutes. "
        "Workspace selection and live mode retain offset semantics.",
    ),
    (
        "retention",
        "Features",
        "Scheduled reversible retention",
        "verified",
        "Scheduling, persistent intervals and baseline protection pass on SQLite/PostgreSQL.",
    ),
    (
        "scrum",
        "Workflows",
        "Scrum backlogs, sprint board and reviews",
        "verified",
        "Browser/API checks cover planning, Done criteria, child work, progress and reviews.",
    ),
    (
        "invariants",
        "Features",
        "Declarative recorded JSON invariants",
        "verified",
        "Typed comparisons preserve unknown/error outcomes and reject incompatible baselines.",
    ),
    (
        "deletion",
        "Production",
        "Permanent deletion lifecycle",
        "verified",
        "Reviewed run/artifact deletion, SQLite/PostgreSQL recovery "
        "and managed backup expiry pass. "
        "Live deletion and expiry stay disabled; unmanaged copies require operator handling.",
    ),
    (
        "identity",
        "Team operation",
        "Identity-provider sign-in",
        "pending",
        "GitHub OAuth/PKCE and role removal pass mock tests. "
        "Live acceptance requires OAuth app settings.",
    ),
    (
        "workers",
        "Team operation",
        "Distributed durable job coordination",
        "verified",
        "SQLite/PostgreSQL process races, crash rollback, fenced retries "
        "and browser workflows pass. "
        "One API plus offline workers; production multi-host qualification remains pending.",
    ),
    (
        "containers",
        "Production",
        "Docker deployment verification",
        "pending",
        "Build and run the image, then exercise persistence and health checks.",
    ),
    (
        "ci",
        "Production",
        "Remote CI execution",
        "pending",
        "A successful local run does not establish remote CI success.",
    ),
    (
        "operations",
        "Production",
        "Load, recovery and upgrade acceptance",
        "pending",
        "Local load smoke, restore and migrations pass. "
        "Production capacity and deployment recovery remain unverified.",
    ),
]


def readiness() -> dict[str, object]:
    return {
        "scope": "Defensive offline assurance release",
        "notice": "Acceptance checkpoints, not percentage completion of the full blueprint.",
        "criteria": [
            dict(zip(("id", "area", "title", "status", "evidence"), row, strict=True))
            for row in CRITERIA
        ],
    }
