"""Loopback-first read-only evidence API plus the bundled trusted demo."""

import json
import os
import re
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

RUN_ID = re.compile(r"run-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}\Z")
MAX_REPORT_BYTES = 32_000_000


def create_app(data_dir: Path | None = None) -> FastAPI:
    root = (data_dir or Path(os.environ.get("BRANCHLAB_DATA_DIR", ".branchlab/runs"))).resolve()
    root.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="BranchLab", version="0.1.0")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])
    demo_lock = threading.Lock()

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
        return {"status": "ok", "version": "0.1.0"}

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
        if path.is_symlink() or not path.is_file():
            raise HTTPException(404, "Artifact not found")
        return FileResponse(path, filename=f"{run_id}-{artifact}")

    @app.post("/api/demo")
    def demo(request: Request):
        # Explicit local fixture only. Never accept repository paths or test code from HTTP.
        origin = request.headers.get("origin")
        if origin:
            from urllib.parse import urlparse
            if urlparse(origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
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
