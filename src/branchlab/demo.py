"""A real two-commit test repository; only the planning inputs are hand-authored."""

import os
import subprocess
from pathlib import Path

from .models import Plan

BASE = '''from fastapi import FastAPI, Header, HTTPException

app = FastAPI()
CACHE_TTL = 60
cache = {}
records = {"alpha": {"1": "alpha-private-note"}, "beta": {"1": "beta-private-note"}}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/settings")
def settings():
    return {"cache_ttl": CACHE_TTL}

@app.get("/notes/{note_id}")
def note(note_id: str, x_tenant: str = Header()):
    if x_tenant not in records:
        raise HTTPException(403, "Unknown tenant")
    key = (x_tenant, note_id)
    if key not in cache:
        if note_id not in records[x_tenant]:
            raise HTTPException(404, "Not found")
        cache[key] = {"tenant": x_tenant, "content": records[x_tenant][note_id]}
    return cache[key]
'''
HEAD = BASE.replace('CACHE_TTL = 60', 'CACHE_TTL = 120').replace('key = (x_tenant, note_id)', 'key = note_id')
CONTRACT = '''# Tenant notes service contract
Each tenant must only receive its own notes, including on cache hits.
The /health endpoint must return status ok.
The existing cache TTL is 60 seconds.
'''
INTENT = '''# Change request: longer cache lifetime
Increase cache TTL from 60 to 120 seconds and simplify the cache key for faster lookup.
Tenant isolation must remain unchanged; no client should receive another tenant's data.
Health endpoint behavior is unchanged.
'''


def create_demo_repo(destination: Path) -> Path:
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("demo destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)

    def git(*args):
        environment = {**os.environ, "GIT_AUTHOR_DATE": "2025-01-01T00:00:00+00:00",
                       "GIT_COMMITTER_DATE": "2025-01-01T00:00:00+00:00",
                       "GIT_AUTHOR_NAME": "BranchLab Demo", "GIT_COMMITTER_NAME": "BranchLab Demo",
                       "GIT_AUTHOR_EMAIL": "demo@branchlab.invalid",
                       "GIT_COMMITTER_EMAIL": "demo@branchlab.invalid"}
        return subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
             "-c", "core.autocrlf=false", "-c", "core.attributesFile=/dev/null", "-C", str(destination), *args],
            check=True, capture_output=True, text=True, env=environment,
        ).stdout.strip()

    git("init", "-b", "main", "--object-format=sha1", "--template=")
    git("config", "user.name", "BranchLab Demo")
    git("config", "user.email", "demo@branchlab.invalid")
    (destination / "service.py").write_text(BASE)
    (destination / "CONTRACT.md").write_text(CONTRACT)
    git("add", ".")
    git("commit", "-m", "Baseline: tenant-isolated cache")
    git("tag", "base")
    (destination / "service.py").write_text(HEAD)
    (destination / "CHANGE.md").write_text(INTENT)
    git("add", ".")
    git("commit", "-m", "Increase TTL and simplify cache key")
    git("tag", "head")
    return destination


def demo_plan() -> Plan:
    def citation(path, line, quote, revision="head"):
        return {"path": path, "line_start": line, "line_end": line, "quote": quote, "revision": revision}

    def step(id, path, tenant=None):
        return {"id": id, "method": "GET", "path": path,
                "headers": [{"name": "x-tenant", "value": tenant}] if tenant else [], "body_json": None}

    def assertion(id, path, value):
        import json
        return {"step_id": id, "target": "json", "path": path, "operator": "eq",
                "expected_json": json.dumps(value)}

    return Plan.model_validate({
        "intent_summary": "TTL should double to 120 seconds. Tenant isolation and health behavior must survive.",
        "probes": [
            {"id": "tenant-isolation", "title": "Cache hits must stay inside tenant boundaries",
             "invariant": "Each tenant only receives its own notes, including cached responses.",
             "intent": "preserve", "severity": "high",
             "rationale": "A tenant-free cache key can reuse alpha's cached note for beta's request.",
             "citations": [citation("CONTRACT.md", 2, CONTRACT.splitlines()[1]),
                           citation("CHANGE.md", 3, INTENT.splitlines()[2])],
             "steps": [step("health", "/health"), step("warm-alpha", "/notes/1", "alpha"),
                       step("read-beta", "/notes/1", "beta")],
             "assertions": [assertion("read-beta", "tenant", "beta"),
                            assertion("read-beta", "content", "beta-private-note")]},
            {"id": "cache-expiration", "title": "Cache TTL intentionally increases",
             "invariant": "The old TTL is 60 seconds; the change request explicitly replaces it with 120.",
             "intent": "change", "severity": "low",
             "rationale": "Use the prior expectation to demonstrate the change; the PR explicitly permits it.",
             "citations": [citation("CHANGE.md", 2, INTENT.splitlines()[1])],
             "steps": [step("settings", "/settings")],
             "assertions": [assertion("settings", "cache_ttl", 60)]},
            {"id": "health-stability", "title": "Health endpoint stays stable",
             "invariant": "Health returns status ok.", "intent": "preserve", "severity": "low",
             "rationale": "A control probe verifies an unaffected documented behavior.",
             "citations": [citation("CONTRACT.md", 3, CONTRACT.splitlines()[2])],
             "steps": [step("health", "/health")],
             "assertions": [assertion("health", "status", "ok")]},
        ],
    })
