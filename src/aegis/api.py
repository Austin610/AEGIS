"""Authenticated, single-user localhost API. No remote targets are contacted."""

import asyncio
import logging
import secrets
import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Send
from starlette.types import Scope as ASGIScope

from aegis import __version__
from aegis.access import Access, NewKey, Principal
from aegis.adapters import default_registry
from aegis.artifact_lifecycle import ArtifactLifecycle
from aegis.assurance import Run, compare
from aegis.data_policy import ApplyRetention, DataPolicies, DataPolicy
from aegis.deletion import ApplyDeletion, ApproveDeletion, DeletionManager, DeletionPreview
from aegis.durable_jobs import DurableJobManager
from aegis.gate import GatePolicy, GateResult, evaluate_gate
from aegis.github_auth import GitHubLogin
from aegis.jobs import Job, JobManager
from aegis.modes import MODES
from aegis.planning import Planning, Sprint, WorkItem
from aegis.policy import Model, Scope, Workspace
from aegis.readiness import readiness
from aegis.repository import Repository, current_actor
from aegis.service import render_report
from aegis.snapshots import Snapshots
from aegis.store import SCHEMA_VERSION, Store
from aegis.workflow import Assessment, Triage, Workflow


class BodyLimit:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        messages: list[Message] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > 1_048_576:
                await JSONResponse({"detail": "Request exceeds 1 MiB"}, status_code=413)(
                    scope, receive, send
                )
                return
            messages.append(message)
            if not message.get("more_body", False):
                break

        async def replay() -> Message:
            if messages:
                return messages.pop(0)
            return await receive()

        await self.app(scope, replay, send)


class Login(Model):
    token: str = Field(min_length=20, max_length=200)


class NewJob(Model):
    adapter_id: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=200)
    payload: dict[str, object]


class NewBaseline(Model):
    name: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")
    run_id: UUID


class ExpiryPrepare(Model):
    name: str = Field(min_length=1, max_length=100)
    fingerprint: str = Field(pattern="^[a-f0-9]{64}$")


class Archive(Model):
    archived: bool


def create_app(
    database: Path | str,
    token: str,
    *,
    durable_jobs: bool = False,
    embedded_worker: bool = True,
    deletion_grace_days: int | None = None,
    pg_recovery_bin: Path | None = None,
    recovery_dir: Path | None = None,
    recovery_retention_days: int | None = None,
) -> FastAPI:
    if len(token) < 32:
        raise ValueError("Local API token must contain at least 32 characters")
    repository = Repository(Store(database))
    registry = default_registry()
    manager = (
        DurableJobManager(repository, registry, worker=embedded_worker)
        if durable_jobs
        else JobManager(repository, registry)
    )
    sessions: dict[str, tuple[float, Principal]] = {}
    access = Access(repository.store)
    policies = DataPolicies(repository.store)
    workflow = Workflow(repository)
    planning = Planning(repository)
    github = GitHubLogin()
    snapshots = Snapshots(repository)
    deletion = DeletionManager(
        repository.store,
        deletion_grace_days,
        pg_bin=pg_recovery_bin,
        recovery_dir=recovery_dir,
        recovery_retention_days=recovery_retention_days,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await manager.start()
        stop = asyncio.Event()

        async def retention_loop() -> None:
            while not stop.is_set():
                try:
                    await asyncio.to_thread(policies.scheduled)
                except Exception:
                    logging.getLogger("aegis.retention").error("Scheduled retention failed")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=60)
                except TimeoutError:
                    pass

        scheduler = asyncio.create_task(retention_loop())
        try:
            yield
        finally:
            stop.set()
            await scheduler
            await manager.shutdown()

    app = FastAPI(
        title="AEGIS",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.repository, app.state.jobs = repository, manager
    app.state.deletion = deletion
    app.add_middleware(BodyLimit)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.middleware("http")
    async def security(request: Request, call_next: Any) -> Response:
        origin = request.headers.get("origin")
        expected = str(request.base_url).rstrip("/")
        if origin and origin != expected:
            return JSONResponse({"detail": "Cross-origin request denied"}, status_code=403)
        response: Response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                    "base-uri 'none'; form-action 'self'"
                ),
            }
        )
        return response

    def principal_for_token(value: str) -> Principal | None:
        if secrets.compare_digest(value, token):
            return Principal(id="owner", role="admin")
        return access.lookup(value)

    async def authenticated(request: Request) -> None:
        authorization = request.headers.get("authorization", "")
        principal = None
        if authorization.startswith("Bearer "):
            principal = principal_for_token(authorization[7:])
        else:
            saved = sessions.get(request.cookies.get("aegis_session", ""))
            if saved and saved[0] > time.monotonic():
                principal = saved[1]
                if principal.id.startswith("github:"):
                    principal = github.principal(principal.id)
                elif principal.id != "owner" and not access.active(principal.id):
                    principal = None
            if (
                principal
                and request.method not in {"GET", "HEAD"}
                and request.headers.get("x-aegis-request") != "1"
            ):
                raise HTTPException(403, "Same-origin application request required")
        if principal is None:
            raise HTTPException(401, "Sign in to AEGIS")
        request.state.principal = principal
        current_actor.set(principal.id)
        workspace = request.path_params.get("workspace_id")
        if (
            workspace
            and principal.role != "admin"
            and UUID(str(workspace)) not in principal.workspace_ids
        ):
            raise HTTPException(403, "Workspace access denied")
        write = request.method not in {"GET", "HEAD"}
        path = request.url.path
        if path.startswith(("/api/access-keys", "/api/recovery/")) or (
            write
            and (
                path == "/api/workspaces"
                or ("/artifacts/" in path and path.endswith("/archive"))
                or path.endswith(
                    (
                        "/scope",
                        "/data-policy",
                        "/retention/apply",
                        "/deletion/preview",
                        "/deletion/prepare",
                        "/deletion/apply",
                    )
                )
            )
        ):
            if principal.role != "admin":
                raise HTTPException(403, "Administrator access required")
        if (
            write
            and principal.role == "reader"
            and path != "/api/session"
            and not path.endswith(("/retention/preview", "/export"))
        ):
            raise HTTPException(403, "Read-only access")

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {"detail": "Invalid input: check required fields and types"}, status_code=422
        )

    @app.exception_handler(ValueError)
    async def value_error(_: Request, __: ValueError) -> JSONResponse:
        return JSONResponse(
            {"detail": "Operation rejected: check scope, identifiers and input"}, status_code=400
        )

    @app.exception_handler(sqlite3.Error)
    async def storage_error(_: Request, __: sqlite3.Error) -> JSONResponse:
        return JSONResponse({"detail": "Storage conflict or unavailable database"}, status_code=409)

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "version": __version__,
            "execution": "offline",
            "schema": SCHEMA_VERSION,
            "job_mode": "durable" if durable_jobs else "local",
        }

    @app.get("/api/auth/providers")
    def auth_providers() -> dict[str, bool]:
        return {"github": github.enabled}

    @app.get("/api/auth/github/start")
    def github_start() -> RedirectResponse:
        state, url = github.begin()
        response = RedirectResponse(url, status_code=302)
        response.set_cookie(
            "aegis_oauth_state",
            state,
            httponly=True,
            samesite="lax",
            max_age=600,
            path="/api/auth/github",
        )
        return response

    @app.get("/api/auth/github/callback")
    async def github_callback(
        request: Request, code: str = Query(max_length=500), state: str = Query(max_length=100)
    ) -> RedirectResponse:
        principal = await asyncio.to_thread(
            github.finish, code, state, request.cookies.get("aegis_oauth_state", "")
        )
        for key, expiry in list(sessions.items()):
            if expiry[0] <= time.monotonic():
                sessions.pop(key)
        if len(sessions) >= 100:
            raise HTTPException(429, "Too many active sessions")
        identity = secrets.token_urlsafe(32)
        sessions[identity] = (time.monotonic() + 8 * 3600, principal)
        response = RedirectResponse("/", status_code=302)
        response.delete_cookie("aegis_oauth_state", path="/api/auth/github")
        response.set_cookie(
            "aegis_session", identity, httponly=True, samesite="strict", max_age=28800
        )
        repository.audit(None, "github.signed_in", {"actor": principal.id})
        return response

    @app.post("/api/session")
    def login(body: Login, response: Response) -> dict[str, bool]:
        principal = principal_for_token(body.token)
        if principal is None:
            raise HTTPException(401, "Invalid local access token")
        for key, expiry in list(sessions.items()):
            if expiry[0] <= time.monotonic():
                sessions.pop(key)
        if len(sessions) >= 100:
            raise HTTPException(429, "Too many active sessions")
        identity = secrets.token_urlsafe(32)
        sessions[identity] = (time.monotonic() + 8 * 3600, principal)
        response.set_cookie(
            "aegis_session", identity, httponly=True, samesite="strict", max_age=28800
        )
        return {"authenticated": True}

    router = APIRouter(prefix="/api", dependencies=[Depends(authenticated)])

    @router.get("/session")
    def session(request: Request) -> dict[str, object]:
        return {"authenticated": True, **request.state.principal.model_dump(mode="json")}

    @router.delete("/session")
    def logout(request: Request, response: Response) -> dict[str, bool]:
        sessions.pop(request.cookies.get("aegis_session", ""), None)
        response.delete_cookie("aegis_session")
        return {"authenticated": False}

    @router.get("/schema")
    def schema() -> dict[str, Any]:
        return app.openapi()

    @router.get("/access-keys")
    def access_keys(
        limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
    ) -> list[dict[str, object]]:
        return access.list(limit, offset)

    @router.post("/access-keys", status_code=201)
    def create_key(body: NewKey, request: Request) -> dict[str, object]:
        result = access.create(body)
        repository.audit(
            None,
            "access_key.created",
            {"actor": request.state.principal.id, "key_id": result["id"], "role": body.role},
        )
        return result

    @router.delete("/access-keys/{key_id}")
    def revoke_key(key_id: UUID, request: Request) -> dict[str, bool]:
        access.revoke(key_id)
        repository.audit(
            None, "access_key.revoked", {"actor": request.state.principal.id, "key_id": str(key_id)}
        )
        return {"revoked": True}

    @router.get("/recovery/expiry")
    def recovery_expiry() -> dict[str, Any]:
        return deletion.retention.preview()

    @router.post("/recovery/expiry/prepare")
    def prepare_recovery_expiry(body: ExpiryPrepare, request: Request) -> dict[str, Any]:
        return deletion.retention.prepare(request.state.principal.id, body.name, body.fingerprint)

    @router.post("/recovery/expiry/apply")
    def apply_recovery_expiry(body: ApplyDeletion, request: Request) -> dict[str, Any]:
        return deletion.retention.apply(request.state.principal.id, body.token, body.confirmation)

    @router.get("/workspaces/{workspace_id}/data-policy")
    def data_policy(workspace_id: UUID) -> DataPolicy:
        return policies.get(workspace_id)

    @router.put("/workspaces/{workspace_id}/data-policy")
    def save_data_policy(workspace_id: UUID, body: DataPolicy, request: Request) -> DataPolicy:
        return policies.save(workspace_id, body, request.state.principal.id)

    @router.post("/workspaces/{workspace_id}/retention/preview")
    def preview_retention(workspace_id: UUID) -> dict[str, object]:
        return policies.preview(workspace_id)

    @router.post("/workspaces/{workspace_id}/deletion/preview")
    def preview_deletion(workspace_id: UUID, body: DeletionPreview) -> dict[str, Any]:
        return deletion.preview(workspace_id, body.grace_days, body.resource)

    @router.post("/workspaces/{workspace_id}/deletion/prepare")
    def prepare_deletion(
        workspace_id: UUID, body: ApproveDeletion, request: Request
    ) -> dict[str, Any]:
        return deletion.prepare(
            workspace_id, request.state.principal.id, body.fingerprint, body.resource
        )

    @router.post("/workspaces/{workspace_id}/deletion/apply")
    def apply_deletion(workspace_id: UUID, body: ApplyDeletion, request: Request) -> dict[str, Any]:
        # Same lock order as snapshot capture: cache then database. No stale cached copies survive.
        with snapshots.lock:
            result = deletion.apply(workspace_id, request.state.principal.id, body)
            snapshots.cache = {
                k: v for k, v in snapshots.cache.items() if v[1][1] != str(workspace_id)
            }
        return result

    @router.post("/workspaces/{workspace_id}/retention/apply")
    def apply_retention(
        workspace_id: UUID, body: ApplyRetention, request: Request
    ) -> dict[str, object]:
        return policies.apply(workspace_id, body, request.state.principal.id)

    @router.post("/workspaces/{workspace_id}/export")
    def export_workspace(workspace_id: UUID, request: Request) -> dict[str, object]:
        try:
            return policies.export(workspace_id, request.state.principal.id)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc

    @router.get("/modes")
    def modes() -> dict[str, dict[str, str]]:
        return MODES

    @router.get("/adapters")
    async def adapters() -> list[dict[str, object]]:
        return [
            {**entry, "healthy": await registry.get(str(entry["id"])).healthcheck()}
            for entry in registry.manifest()
        ]

    @router.get("/workspaces")
    def workspaces(
        request: Request, limit: int = Query(200, ge=1, le=200), offset: int = Query(0, ge=0)
    ) -> list[Workspace]:
        return repository.workspaces(
            limit=limit,
            offset=offset,
            allowed_ids=None
            if request.state.principal.role == "admin"
            else request.state.principal.workspace_ids,
        )

    @router.get("/workspaces/{workspace_id}")
    def workspace_detail(workspace_id: UUID) -> Workspace:
        return repository.store.workspace(workspace_id)

    @router.post("/workspaces", status_code=201)
    def new_workspace(body: Workspace) -> Workspace:
        repository.store.add_workspace(body)
        repository.audit(body.id, "workspace.created", {"mode": body.mode})
        return body

    @router.get("/workspaces/{workspace_id}/scope")
    def get_scope(workspace_id: UUID) -> Scope | None:
        return repository.scope(workspace_id)

    @router.put("/workspaces/{workspace_id}/scope")
    def save_scope(workspace_id: UUID, body: Scope) -> Scope:
        if body.workspace_id != workspace_id:
            raise HTTPException(400, "Scope workspace mismatch")
        repository.save_scope(body)
        return body

    @router.get("/workspaces/{workspace_id}/runs")
    def runs(
        workspace_id: UUID,
        archived: bool = False,
        limit: int = Query(200, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> list[Run]:
        return repository.runs(workspace_id, archived=archived, limit=limit, offset=offset)

    @router.get("/workspaces/{workspace_id}/runs/{run_id}")
    def run(workspace_id: UUID, run_id: UUID) -> Run:
        return repository.store.run(run_id, workspace_id)

    @router.get("/workspaces/{workspace_id}/runs/{run_id}/evidence")
    def evidence(workspace_id: UUID, run_id: UUID) -> dict[str, object]:
        run = repository.store.run(run_id, workspace_id)
        return repository.store.evidence(run.evidence_id, workspace_id)

    @router.get("/workspaces/{workspace_id}/runs/{run_id}/report", response_class=HTMLResponse)
    def report(workspace_id: UUID, run_id: UUID) -> str:
        run = repository.store.run(run_id, workspace_id)
        return render_report(run, repository.store.evidence(run.evidence_id, workspace_id))

    @router.put("/workspaces/{workspace_id}/runs/{run_id}/archive")
    def archive(workspace_id: UUID, run_id: UUID, body: Archive) -> dict[str, bool]:
        repository.archive(workspace_id, run_id, body.archived)
        return {"archived": body.archived}

    @router.get("/workspaces/{workspace_id}/baselines")
    def baselines(
        workspace_id: UUID, limit: int = Query(200, ge=1, le=200), offset: int = Query(0, ge=0)
    ) -> list[dict[str, str]]:
        return repository.baselines(workspace_id, limit=limit, offset=offset)

    @router.post("/workspaces/{workspace_id}/baselines", status_code=201)
    def baseline(workspace_id: UUID, body: NewBaseline) -> dict[str, str]:
        repository.store.baseline(workspace_id, body.name, body.run_id)
        repository.audit(
            workspace_id, "baseline.created", {"name": body.name, "run_id": str(body.run_id)}
        )
        return {"name": body.name, "run_id": str(body.run_id)}

    @router.get("/workspaces/{workspace_id}/compare")
    def comparison(workspace_id: UUID, baseline: str, run_id: UUID) -> dict[str, str]:
        return compare(
            repository.store.get_baseline(workspace_id, baseline),
            repository.store.run(run_id, workspace_id),
        )

    @router.put("/workspaces/{workspace_id}/artifacts/{artifact_id}/archive")
    def archive_artifact(workspace_id: UUID, artifact_id: UUID, body: Archive) -> dict[str, bool]:
        ArtifactLifecycle(repository.store).archive(workspace_id, artifact_id, body.archived)
        return {"archived": body.archived}

    @router.get("/workspaces/{workspace_id}/artifacts")
    def artifacts(
        workspace_id: UUID, limit: int = Query(200, ge=1, le=200), offset: int = Query(0, ge=0)
    ) -> list[dict[str, object]]:
        return repository.artifacts(workspace_id, limit=limit, offset=offset)

    @router.post("/workspaces/{workspace_id}/gate")
    def gate(workspace_id: UUID, baseline: str, run_id: UUID, policy: GatePolicy) -> GateResult:
        result = evaluate_gate(
            repository.store.get_baseline(workspace_id, baseline),
            repository.store.run(run_id, workspace_id),
            policy,
        )
        repository.audit(
            workspace_id,
            "gate.evaluated",
            {
                "run_id": str(run_id),
                "baseline": baseline,
                "status": result.status,
            },
        )
        return result

    @router.get("/workspaces/{workspace_id}/audit")
    def audit(
        workspace_id: UUID, limit: int = Query(200, ge=1, le=200), offset: int = Query(0, ge=0)
    ) -> list[dict[str, object]]:
        return repository.events(workspace_id, limit=limit, offset=offset)

    @router.get("/workspaces/{workspace_id}/jobs")
    def jobs(
        workspace_id: UUID, limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)
    ) -> list[Job]:
        return manager.list(workspace_id, limit=limit, offset=offset)

    @router.post("/workspaces/{workspace_id}/jobs", status_code=202)
    async def submit(workspace_id: UUID, body: NewJob) -> Job:
        return await manager.submit(workspace_id, body.adapter_id, body.target, body.payload)

    @router.post("/workspaces/{workspace_id}/jobs/{job_id}/cancel")
    async def cancel(workspace_id: UUID, job_id: UUID) -> Job:
        return await manager.cancel(workspace_id, job_id)

    @router.get("/readiness")
    def release_readiness() -> dict[str, object]:
        return readiness()

    @router.get("/workspaces/{workspace_id}/planning")
    def project_planning(workspace_id: UUID) -> dict[str, Any]:
        return planning.snapshot(workspace_id)

    @router.get("/workspaces/{workspace_id}/snapshots/{resource}")
    def list_snapshot(
        workspace_id: UUID,
        resource: str,
        request: Request,
        cursor: str | None = Query(default=None, max_length=100),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        archived: bool = False,
    ) -> dict[str, Any]:
        return snapshots.page(
            request.state.principal.id, workspace_id, resource, cursor, limit, offset, archived
        )

    @router.put("/workspaces/{workspace_id}/items/{identity}")
    def save_work_item(workspace_id: UUID, identity: UUID, body: WorkItem) -> dict[str, Any]:
        return planning.save(workspace_id, "item", identity, body)

    @router.put("/workspaces/{workspace_id}/sprints/{identity}")
    def save_sprint(workspace_id: UUID, identity: UUID, body: Sprint) -> dict[str, Any]:
        return planning.save(workspace_id, "sprint", identity, body)

    @router.get("/workspaces/{workspace_id}/workflow")
    def workflow_snapshot(workspace_id: UUID) -> dict[str, Any]:
        return workflow.snapshot(workspace_id)

    @router.put("/workspaces/{workspace_id}/findings/{finding_id}")
    def triage_finding(workspace_id: UUID, finding_id: str, body: Triage) -> dict[str, Any]:
        return workflow.save(workspace_id, "triage", finding_id, body)

    @router.put("/workspaces/{workspace_id}/coverage/{control_id}")
    def assess_control(workspace_id: UUID, control_id: UUID, body: Assessment) -> dict[str, Any]:
        return workflow.save(workspace_id, "coverage", str(control_id), body)

    @router.get("/workspaces/{workspace_id}/stakeholder-report", response_class=HTMLResponse)
    def stakeholder_report(workspace_id: UUID) -> str:
        if not policies.get(workspace_id).export_allowed:
            raise HTTPException(403, "Workspace export is disabled")
        return workflow.report(workspace_id)

    app.include_router(router)
    web = Path(__file__).parent / "web"

    @app.get("/", response_class=FileResponse)
    def index() -> Path:
        return web / "index.html"

    @app.get("/app.js", response_class=FileResponse)
    def script() -> Path:
        return web / "app.js"

    @app.get("/style.css", response_class=FileResponse)
    def style() -> Path:
        return web / "style.css"

    @app.get("/planning.js", response_class=FileResponse)
    def planning_script() -> Path:
        return web / "planning.js"

    return app
