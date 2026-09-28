"""Authenticated evidence API, bounded PR submission and signed App webhooks."""

import json
import hmac
import os
import re
import threading
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse

RUN_ID = re.compile(r"run-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}\Z")
MAX_REPORT_BYTES = 32_000_000


def create_app(data_dir: Path | None = None) -> FastAPI:
    root = (data_dir or Path(os.environ.get("BRANCHLAB_DATA_DIR", ".branchlab/runs"))).resolve()
    root.mkdir(parents=True, exist_ok=True)
    from . import __version__
    public_origin = os.environ.get("BRANCHLAB_PUBLIC_ORIGIN", "").rstrip("/")
    token = os.environ.get("BRANCHLAB_API_TOKEN", "")
    allowed_hosts = ["localhost", "127.0.0.1", "[::1]"]
    if public_origin:
        origin = urlparse(public_origin)
        if (origin.scheme != "https" or not origin.hostname or origin.username or origin.password
                or origin.path or origin.query or origin.fragment or len(token) < 32):
            raise ValueError("Remote API requires an HTTPS origin and a BRANCHLAB_API_TOKEN of at least 32 characters")
        allowed_hosts.extend([origin.hostname, "api"])
    elif token and len(token) < 32:
        raise ValueError("BRANCHLAB_API_TOKEN must contain at least 32 characters")
    app = FastAPI(title="BranchLab", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        # Webhooks authenticate the raw body with an independent HMAC secret.
        exempt = request.url.path in {"/api/health", "/api/github/webhook"}
        if token and not exempt:
            expected = "Bearer " + token
            provided = request.headers.get("authorization", "")
            if not hmac.compare_digest(provided.encode(), expected.encode()):
                return JSONResponse({"detail": "Authentication required"}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    demo_lock = threading.Lock()
    app_config = None
    job_queue = None
    if os.environ.get("BRANCHLAB_GITHUB_APP_ID"):
        from .github_app import AppConfig, JobQueue, create_webhook_router
        app_config = AppConfig.from_env()
        job_queue = JobQueue(app_config.state_dir / "queue.sqlite3")
        app.include_router(create_webhook_router(app_config, job_queue))

    def read_report(path: Path):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_REPORT_BYTES:
            raise HTTPException(404, "Report unavailable")
        try:
            value = json.loads(path.read_text())
            if not isinstance(value, dict) or value.get("id") != path.parent.name:
                raise ValueError("Mismatched report")
            return value
        except (OSError, ValueError):
            raise HTTPException(404, "Report unavailable") from None

    def run_directory(run_id: str):
        if not RUN_ID.fullmatch(run_id):
            raise HTTPException(404, "Run not found")
        path = root / run_id
        if path.is_symlink() or not path.is_dir() or path.resolve().parent != root:
            raise HTTPException(404, "Run not found")
        return path

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/jobs")
    def jobs():
        return job_queue.list_jobs(limit=50) if job_queue else []

    @app.get("/api/jobs/{job_id}")
    def job(job_id: int):
        result = job_queue.get(job_id) if job_queue and job_id > 0 else None
        if result is None:
            raise HTTPException(404, "Investigation not found")
        return result

    @app.post("/api/investigations", status_code=202)
    async def submit(request: Request):
        if app_config is None or job_queue is None:
            raise HTTPException(503, "GitHub App is not configured")
        supplied_origin = request.headers.get("origin")
        if supplied_origin and supplied_origin != public_origin and urlparse(supplied_origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise HTTPException(403, "Dashboard origin required")
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            raise HTTPException(415, "JSON required")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 8192:
                raise HTTPException(413, "Request too large")
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict) or set(payload) != {"pr_url"} or not isinstance(payload["pr_url"], str):
                raise ValueError("invalid request")
        except (ValueError, UnicodeError):
            raise HTTPException(422, "A single pr_url is required") from None
        from .github_app import enqueue_pr_url
        from starlette.concurrency import run_in_threadpool
        try:
            return await run_in_threadpool(enqueue_pr_url, payload["pr_url"], app_config, job_queue)
        except ValueError:
            raise HTTPException(422, "Invalid or unauthorized pull request") from None
        except Exception:
            raise HTTPException(502, "Unable to queue this pull request; check App installation and repository access") from None

    @app.get("/api/runs")
    def list_runs():
        reports = []
        candidates = sorted((p for p in root.iterdir() if RUN_ID.fullmatch(p.name) and not p.is_symlink()), reverse=True)[:100]
        for directory in candidates:
            path = directory / "report.json"
            try:
                reports.append(read_report(path))
            except (OSError, HTTPException):
                continue
        return reports

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        path = run_directory(run_id) / "report.json"
        return read_report(path)

    @app.get("/api/runs/{run_id}/{artifact}")
    def artifact(run_id: str, artifact: str):
        if artifact not in {"report.md", "report.json", "plan.json"}:
            raise HTTPException(404, "Artifact not found")
        path = run_directory(run_id) / artifact
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_REPORT_BYTES:
            raise HTTPException(404, "Artifact not found")
        return FileResponse(path, filename=f"{run_id}-{artifact}")

    @app.post("/api/demo")
    def demo(request: Request):
        # Explicit local fixture only. Never accept repository paths or test code from HTTP.
        origin = request.headers.get("origin")
        if origin:
            if origin != public_origin and urlparse(origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise HTTPException(403, "Local dashboard origin required")
        if not demo_lock.acquire(blocking=False):
            raise HTTPException(409, "A demo investigation is already running")
        try:
            from .demo import create_demo_repo, demo_plan
            from .engine import investigate
            repo = create_demo_repo(root.parent / "fixtures" / uuid.uuid4().hex / "tenant-cache")
            return investigate(repo, "base", "head", demo_plan(), output=root,
                               provider={"name": "fixture", "model": "deterministic-demo", "live_ai": False,
                                         "input_tokens": 0, "output_tokens": 0},
                               runner="subprocess", trusted_local=True)
        finally:
            demo_lock.release()

    return app
