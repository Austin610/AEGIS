# Deployment acceptance record

Status: small local Docker pilot targets accepted; production qualification remains open.
Passing CI and the disposable local drill does not complete production qualification.

## Agree on the deployment

Record these before setting acceptance thresholds:

| Decision | Required detail |
| --- | --- |
| Target | Local Windows computer, private Docker host, or shared hosted service |
| Storage | SQLite on local disk or PostgreSQL version, host and backup operator |
| Workload | Concurrent users, daily jobs, retained artifacts and typical input sizes |
| Responsiveness | Maximum acceptable p95 read latency and job completion time |
| Recovery time objective | Maximum acceptable time to restore usable service |
| Recovery point objective | Maximum acceptable age of data lost after a failure |
| Ownership | Person responsible for backups, monitoring, upgrades and incident recovery |

The supported topology is one API process with optional durable offline workers.
The existing server and OAuth callback are configured for loopback use. A shared
hosted service requires separate HTTPS, cookie and proxy work before acceptance.

## Collect evidence

1. Record the application commit, OS/runtime, database version, deployment
   configuration and workload shape. Exclude secrets and personal records.
2. Run `python scripts/operational_drill.py` from the project environment. It creates
   disposable data and records upgrade readiness, restart verification, backup size
   and duration, and restore verification duration in
   `.aegis/verification/operational-drill.json`. Restore timing starts before creating
   the restore directory and ends after checking recovered data and revoked access.
   Restart timing includes stopping the process and verifying access after startup.
3. Repeat representative workload and failure checks on an isolated instance of
   the selected deployment. The small SQLite fixture is a development baseline;
   it cannot establish PostgreSQL recovery behavior or production capacity.
4. Verify the actual backup schedule and an independently stored backup. Restore
   into a separate destination, verify evidence and permissions, and measure the
   data age and elapsed recovery time against the agreed objectives.
5. Rehearse upgrade and rollback on a copy of representative data. Record migration
   duration, job handling, operator steps and any data lost by rollback. Drain and
   stop workers before transfer or rollback.
6. Verify live GitHub login and role removal separately using the procedure in
   [GitHub connection](github-connection.md).

## Sign-off

For each requirement, record its target, observed result, evidence location,
date and operator. Record unresolved failures explicitly. Update the operations
readiness checkpoint only after the selected deployment meets its requirements.
Do not infer acceptance from synthetic latency results or test counts.

## Local Docker baseline — 2026-11-02

Run `python scripts/container_qualification.py` after building `aegis-local`.
The expanded disposable Windows Docker Desktop run passed with 12 workspaces,
12 durable jobs, 12 artifacts, and 100 reads at concurrency four. The 12 jobs and
artifacts survived a verified restore into a separate container. The latest sample
had p95 read latency 35.562 ms, container startup 1397.915 ms, backup 15.118 ms
(212,992 bytes), and restore verification 1192.37 ms. Evidence is stored locally in
`.aegis/verification/container-qualification.json`. The drill uses synthetic
small data and does not establish a production RTO, RPO, backup schedule, or
representative capacity. The operations checkpoint remains pending.

## Small local pilot acceptance — 2026-11-02

The selected pilot targets are four concurrent requests, 12 durable jobs, p95
read latency at most 250 ms, and verified restore within 30 seconds. The
checkpoint recovery target is zero missing workspace, job, or artifact records
from the completed backup. The qualification script now enforces these limits.

Observed: 12 workspaces, 12 succeeded jobs and 12 artifacts; 100 reads with
p95 40.579 ms; verified restore in 2257.397 ms; no missing checkpoint
records. The separate schema-5 upgrade drill passed with 12 jobs and 100 reads.
This qualifies the **small local Docker pilot** only. An ongoing backup schedule,
maximum backup age, production RTO/RPO and larger workload remain unqualified.
