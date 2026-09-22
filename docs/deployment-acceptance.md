# Deployment acceptance record

Status: pending selection of deployment target and operating requirements.
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
