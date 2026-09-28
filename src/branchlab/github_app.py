"""Installation-scoped GitHub App ingress and durable, fail-closed investigations.

The webhook accepts data, not executable jobs. Repository identity, application
entry point, planner and runner are all controlled by the operator. GitHub App
tokens are short-lived, never stored, and are not passed to investigated code.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import hmac
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid

import httpx
from fastapi import APIRouter, HTTPException, Request

from .github import _destination, _parse_url

MAX_WEBHOOK_BYTES = 1_000_000
MAX_API_BYTES = 4_000_000
MAX_PENDING = 500
MAX_ATTEMPTS = 3
LEASE_SECONDS = 120
SHA = re.compile(r"[a-f0-9]{40}\Z")
FULL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}\Z")
ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}


class AppError(RuntimeError):
    """Deliberately credential-free operational error."""


class SnapshotChanged(AppError):
    """The queued revision/description is no longer the active PR snapshot."""


def _integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


@dataclass(frozen=True)
class RepositoryPolicy:
    installation_id: int
    repository_id: int
    full_name: str
    app_entry: str = "service:app"

    def __post_init__(self):
        if not _integer(self.installation_id) or not _integer(self.repository_id):
            raise AppError("Installation and repository IDs must be positive integers.")
        if not FULL_NAME.fullmatch(self.full_name) or self.full_name.split("/")[1] in {".", ".."}:
            raise AppError("Allowlist repository must be OWNER/REPOSITORY.")
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", self.app_entry):
            raise AppError("Allowlist app_entry must be module:attribute.")


@dataclass(frozen=True)
class AppConfig:
    app_id: int
    private_key_file: Path = field(repr=False)
    webhook_secret: str = field(repr=False)
    repositories: tuple[RepositoryPolicy, ...]
    state_dir: Path = Path(".branchlab/github")
    report_dir: Path = Path(".branchlab/runs")
    model: str | None = None
    planner_provider: str = "openai"

    def __post_init__(self):
        if not _integer(self.app_id) or len(self.webhook_secret.encode()) < 32:
            raise AppError("App ID and a webhook secret of at least 32 bytes are required.")
        if not self.repositories or len(self.repositories) > 100:
            raise AppError("Configure between one and 100 allowed repositories.")
        if self.planner_provider not in {"openai", "openclaw"}:
            raise AppError("App planner must be tool-free openai or openclaw inference.")
        keys = [(r.installation_id, r.repository_id) for r in self.repositories]
        if len(set(keys)) != len(keys) or len({r.full_name.lower() for r in self.repositories}) != len(keys):
            raise AppError("Repository allowlist entries must be unique.")

    @classmethod
    def from_env(cls) -> AppConfig:
        try:
            entries = json.loads(os.environ["BRANCHLAB_GITHUB_ALLOWLIST_JSON"])
            if not isinstance(entries, list):
                raise ValueError()
            return cls(
                app_id=int(os.environ["BRANCHLAB_GITHUB_APP_ID"]),
                private_key_file=Path(os.environ["BRANCHLAB_GITHUB_APP_PRIVATE_KEY_FILE"]),
                webhook_secret=os.environ["BRANCHLAB_GITHUB_WEBHOOK_SECRET"],
                repositories=tuple(RepositoryPolicy(**entry) for entry in entries),
                state_dir=Path(os.environ.get("BRANCHLAB_GITHUB_STATE_DIR", ".branchlab/github")),
                report_dir=Path(os.environ.get("BRANCHLAB_DATA_DIR", ".branchlab/runs")),
                model=os.environ.get("BRANCHLAB_MODEL"),
                planner_provider=os.environ.get("BRANCHLAB_PLANNER_PROVIDER", "openai"),
            )
        except (KeyError, ValueError, TypeError):
            raise AppError("GitHub App environment configuration is missing or invalid.") from None

    def policy(self, installation: int, repository: int, full_name: str) -> RepositoryPolicy:
        if not _integer(installation) or not _integer(repository) or not isinstance(full_name, str):
            raise AppError("Invalid installation/repository identity.")
        for entry in self.repositories:
            if (entry.installation_id == installation and entry.repository_id == repository
                    and entry.full_name.lower() == full_name.lower()):
                return entry
        raise AppError("Installation/repository is not in the operator allowlist.")


def verify_signature(body: bytes, signature: str | None, secret: str) -> bool:
    if not signature or not re.fullmatch(r"sha256=[a-f0-9]{64}", signature):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _snapshot(pr: dict, policy: RepositoryPolicy) -> dict:
    """Validate all fields used for identity, git refs or model context."""
    try:
        number = pr["number"]
        if not _integer(number) or pr["state"] != "open" or not isinstance(pr.get("draft"), bool):
            raise ValueError()
        base, head = pr["base"], pr["head"]
        repository = base["repo"]
        if (not _integer(repository["id"]) or repository["id"] != policy.repository_id
                or repository["full_name"].lower() != policy.full_name.lower()):
            raise ValueError()
        if not SHA.fullmatch(base["sha"]) or not SHA.fullmatch(head["sha"]):
            raise ValueError()
        if not _integer(head["repo"]["id"]):
            raise ValueError()
        if not isinstance(pr["title"], str) or not isinstance(pr.get("body") or "", str):
            raise ValueError()
        description = pr["title"] + "\n\n" + (pr.get("body") or "")
        if len(description.encode()) > 100_000:
            raise ValueError()
        return {"installation_id": policy.installation_id, "repository_id": policy.repository_id,
                "repository": policy.full_name, "pr_number": number,
                "base_sha": base["sha"], "head_sha": head["sha"],
                "head_repository_id": head["repo"]["id"],
                "description_sha256": hashlib.sha256(description.encode()).hexdigest()}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise AppError("PR metadata has invalid or mismatched identity/revision fields.") from None


class JobQueue:
    """SQLite transaction queue. Deliveries/snapshots deduplicate across restarts.

    Workers heartbeat leases. A killed process may be reclaimed after two
    minutes; a lease token fences late writers. Execution is at least once after
    crashes, not an impossible claim of atomic exactly-once network delivery.
    """

    def __init__(self, path: Path):
        self.path = Path(path).absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise AppError("Queue path must not be a symlink.")
        with self._db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL,
                    payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0, lease TEXT, lease_until REAL,
                    check_run_id INTEGER, report_id TEXT, error TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS deliveries (
                    delivery TEXT PRIMARY KEY, digest TEXT NOT NULL, job_id INTEGER NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id));
            """)
        self.path.chmod(0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, delivery: str, digest: str, snapshot: dict, *, retry_failed: bool = False) -> dict:
        encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        now = time.time()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT digest,job_id FROM deliveries WHERE delivery=?", (delivery,)).fetchone()
            if old:
                if old["digest"] != digest:
                    raise AppError("A delivery ID was reused for different content.")
                job = db.execute("SELECT id,status FROM jobs WHERE id=?", (old["job_id"],)).fetchone()
                return {"job_id": job["id"], "status": job["status"], "duplicate": True}
            old = db.execute("SELECT id,status FROM jobs WHERE fingerprint=?", (fingerprint,)).fetchone()
            if old:
                job_id, status, duplicate = old["id"], old["status"], True
                if retry_failed and status == "failed":
                    db.execute("UPDATE jobs SET status='queued',attempts=0,error=NULL,updated_at=? WHERE id=?",
                               (now, job_id))
                    status = "queued"
            else:
                count = db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
                if count >= MAX_PENDING:
                    raise AppError("Investigation queue is full; retry later.")
                job_id = db.execute("INSERT INTO jobs(fingerprint,payload,created_at,updated_at) VALUES(?,?,?,?)",
                                    (fingerprint, encoded, now, now)).lastrowid
                status, duplicate = "queued", False
            db.execute("INSERT INTO deliveries(delivery,digest,job_id) VALUES(?,?,?)", (delivery, digest, job_id))
        return {"job_id": job_id, "status": status, "duplicate": duplicate}

    @staticmethod
    def _public(row) -> dict:
        payload = json.loads(row["payload"])
        return {"job_id": row["id"], "status": row["status"], "attempts": row["attempts"],
                **{k: payload[k] for k in ("repository", "pr_number", "base_sha", "head_sha")},
                **{k: row[k] for k in ("report_id", "error", "created_at", "updated_at")}}

    def get(self, job_id: int) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._public(row) if row else None

    def list_jobs(self, limit: int = 50) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
        return [self._public(row) for row in rows]

    def claim(self) -> dict | None:
        now, lease = time.time(), uuid.uuid4().hex
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE jobs SET status='failed',error='Worker lease expired repeatedly.',updated_at=? "
                       "WHERE status='running' AND lease_until<? AND attempts>=?", (now, now, MAX_ATTEMPTS))
            row = db.execute("SELECT * FROM jobs WHERE status='queued' OR "
                             "(status='running' AND lease_until<? AND attempts<?) ORDER BY id LIMIT 1",
                             (now, MAX_ATTEMPTS)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status='running',attempts=attempts+1,lease=?,lease_until=?,updated_at=? "
                       "WHERE id=?", (lease, now + LEASE_SECONDS, now, row["id"]))
        return {"job_id": row["id"], "lease": lease, "payload": json.loads(row["payload"]),
                "check_run_id": row["check_run_id"], "report_id": row["report_id"],
                "attempts": row["attempts"] + 1}

    def heartbeat(self, job: dict) -> bool:
        with self._db() as db:
            count = db.execute("UPDATE jobs SET lease_until=?,updated_at=? "
                               "WHERE id=? AND lease=? AND status='running'",
                               (time.time() + LEASE_SECONDS, time.time(), job["job_id"], job["lease"])).rowcount
        return count == 1

    def checkpoint(self, job: dict, *, check_run_id: int | None = None, report_id: str | None = None):
        with self._db() as db:
            count = db.execute("UPDATE jobs SET check_run_id=coalesce(?,check_run_id),"
                               "report_id=coalesce(?,report_id),updated_at=? WHERE id=? AND lease=? AND status='running'",
                               (check_run_id, report_id, time.time(), job["job_id"], job["lease"])).rowcount
        if count != 1:
            raise AppError("Worker lease was lost; result cannot be committed.")

    def finish(self, job: dict, status: str, *, error: str | None = None):
        if status not in {"completed", "failed", "skipped"}:
            raise ValueError("Invalid final job status")
        with self._db() as db:
            count = db.execute("UPDATE jobs SET status=?,error=?,lease=NULL,lease_until=NULL,updated_at=? "
                               "WHERE id=? AND lease=? AND status='running'",
                               (status, error, time.time(), job["job_id"], job["lease"])).rowcount
        if count != 1:
            raise AppError("Worker lease was lost; result cannot be committed.")


def create_webhook_router(config: AppConfig, queue: JobQueue) -> APIRouter:
    router = APIRouter()

    @router.post("/api/github/webhook", status_code=202)
    async def webhook(request: Request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_WEBHOOK_BYTES:
                raise HTTPException(413, "Webhook body exceeds the size limit")
        if not verify_signature(bytes(body), request.headers.get("x-hub-signature-256"), config.webhook_secret):
            raise HTTPException(401, "Invalid webhook signature")
        delivery = request.headers.get("x-github-delivery", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", delivery):
            raise HTTPException(400, "Missing or invalid delivery identifier")
        event = request.headers.get("x-github-event")
        if event not in {"pull_request", "ping"}:
            return {"status": "ignored", "reason": "unsupported event"}
        try:
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ValueError()
            if event == "ping":
                return {"status": "pong"}
            if value.get("action") not in ACTIONS:
                return {"status": "ignored", "reason": "unsupported PR action"}
            repo = value["repository"]
            policy = config.policy(value["installation"]["id"], repo["id"], repo["full_name"])
            pr = value["pull_request"]
            snapshot = _snapshot(pr, policy)
            if value.get("number") != snapshot["pr_number"]:
                raise ValueError()
            if pr["draft"]:
                return {"status": "ignored", "reason": "draft PR"}
            return queue.enqueue(delivery, hashlib.sha256(body).hexdigest(), snapshot)
        except AppError as exc:
            code = 503 if "queue is full" in str(exc) else 400
            raise HTTPException(code, str(exc)) from None
        except (KeyError, TypeError, ValueError, AttributeError):
            raise HTTPException(400, "Malformed webhook payload") from None

    return router


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


class GitHubAppClient:
    """Fixed-origin GitHub REST client; never follows redirects with tokens."""

    def __init__(self, config: AppConfig, *, transport=None):
        self.config = config
        self.transport = transport

    def _jwt(self) -> str:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        try:
            path = self.config.private_key_file
            if path.stat().st_size > 32_768:
                raise ValueError()
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
            if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
                raise ValueError()
            now = int(time.time())
            head = _b64(b'{"alg":"RS256","typ":"JWT"}')
            body = _b64(json.dumps({"iat": now - 60, "exp": now + 540, "iss": str(self.config.app_id)}).encode())
            payload = head + "." + body
            signature = key.sign(payload.encode(), padding.PKCS1v15(), hashes.SHA256())
            return payload + "." + _b64(signature)
        except (OSError, ValueError, TypeError):
            raise AppError("GitHub App private key is unavailable or invalid (RSA 2048+ required).") from None

    def request(self, method: str, path: str, token: str, payload: dict | None = None) -> dict:
        if not path.startswith("/") or "://" in path or ".." in path or path.startswith("//"):
            raise AppError("Invalid GitHub API path.")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "BranchLab/0.2"}
        try:
            with httpx.Client(base_url="https://api.github.com", headers=headers, timeout=30,
                              follow_redirects=False, transport=self.transport, trust_env=False) as client:
                with client.stream(method, path, json=payload) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        raise AppError(f"GitHub API request failed (HTTP {response.status_code}).")
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_API_BYTES:
                            raise AppError("GitHub API response exceeded the size limit.")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except AppError:
            raise
        except (httpx.HTTPError, ValueError, OSError):
            raise AppError("GitHub API request failed; check connectivity and App permissions.") from None

    def installation_token(self, policy: RepositoryPolicy) -> str:
        response = self.request("POST", f"/app/installations/{policy.installation_id}/access_tokens", self._jwt(),
                                {"repository_ids": [policy.repository_id],
                                 "permissions": {"contents": "read", "pull_requests": "read", "checks": "write"}})
        token = response.get("token")
        if not isinstance(token, str) or not token or len(token) > 4096 or any(c.isspace() for c in token):
            raise AppError("GitHub returned an invalid installation token.")
        return token

    def read_pr(self, policy: RepositoryPolicy, number: int, token: str) -> dict:
        value = self.request("GET", f"/repos/{policy.full_name}/pulls/{number}", token)
        snapshot = _snapshot(value, policy)
        if snapshot["pr_number"] != number:
            raise AppError("GitHub returned a different pull request.")
        return value


def enqueue_pr_url(pr_url: str, config: AppConfig, queue: JobQueue, *, client=None) -> dict:
    """For the authenticated owner endpoint, never accepts runner/model options."""
    try:
        owner, name, number = _parse_url(pr_url)
    except Exception:
        raise AppError("Expected a GitHub pull-request URL.") from None
    policy = next((p for p in config.repositories if p.full_name.lower() == f"{owner}/{name}".lower()), None)
    if policy is None:
        raise AppError("Repository is not in the operator allowlist.")
    client = client or GitHubAppClient(config)
    token = client.installation_token(policy)
    pr = client.read_pr(policy, number, token)
    if pr["draft"]:
        raise AppError("Draft pull requests are not investigated automatically.")
    snapshot = _snapshot(pr, policy)
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    return queue.enqueue("manual-" + uuid.uuid4().hex, digest, snapshot, retry_failed=True)


def _git(args: list[str], env: dict, timeout=120) -> str:
    try:
        result = subprocess.run(args, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=timeout, check=False)
        if result.returncode or len(result.stdout) > 1_000_000:
            raise AppError("Git snapshot operation failed; check repository access and size.")
        return result.stdout.decode("utf-8", "replace").strip()
    except (OSError, subprocess.TimeoutExpired):
        raise AppError("Git snapshot operation failed or timed out.") from None


def fetch_app_snapshot(client: GitHubAppClient, policy: RepositoryPolicy, snapshot: dict,
                       destination: Path, token: str) -> dict:
    before = client.read_pr(policy, snapshot["pr_number"], token)
    if before["draft"] or _snapshot(before, policy) != snapshot:
        raise SnapshotChanged("PR changed after enqueue; stale job skipped.")
    target = _destination(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="branchlab-app-git-") as directory:
        credential = Path(directory) / "askpass"
        # The generated helper is fixed operator code, not any file from the PR.
        credential.write_text('#!/bin/sh\ncase "$1" in *Username*) printf "%s\\n" "x-access-token";; '
                              '*) printf "%s\\n" "$BRANCHLAB_INSTALLATION_TOKEN";; esac\n')
        credential.chmod(0o700)
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": directory, "LANG": "C.UTF-8",
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_CONFIG_SYSTEM": os.devnull, "GIT_TERMINAL_PROMPT": "0",
               "GIT_ASKPASS": str(credential), "BRANCHLAB_INSTALLATION_TOKEN": token}
        git = ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "credential.helper=",
               "-c", "protocol.file.allow=never", "-c", "protocol.ext.allow=never",
               "-c", "http.followRedirects=false", "-c", "fetch.fsckObjects=true",
               "-c", "transfer.fsckObjects=true"]
        _git(git + ["init", "--bare", str(target)], env)
        # Never clone a fork URL from the event; GitHub exposes PR objects via the base repo.
        _git(git + ["-C", str(target), "fetch", "--no-tags", "--depth=1",
                    "--", f"https://github.com/{policy.full_name}.git", snapshot["base_sha"], snapshot["head_sha"]], env)
        for sha in (snapshot["base_sha"], snapshot["head_sha"]):
            if _git(git + ["-C", str(target), "rev-parse", "--verify", sha + "^{commit}"], env) != sha:
                raise AppError("Fetched Git object did not match the immutable revision.")
    after = client.read_pr(policy, snapshot["pr_number"], token)
    if after["draft"] or _snapshot(after, policy) != snapshot:
        raise SnapshotChanged("PR changed during fetch; snapshot rejected.")
    return {"repo": target, "description": before["title"] + "\n\n" + (before.get("body") or "")}


def _safe_text(value, maximum=300) -> str:
    # Findings/paths are untrusted; do not allow HTML, markdown links, mentions or controls.
    text = "".join(c for c in str(value) if c.isprintable())[:maximum]
    text = re.sub(r"([\\`*_{}\[\]()#+.!|~-])", r"\\\1", text)
    return html.escape(text, quote=True).replace("@", "&#64;")


def check_output(report: dict) -> dict:
    """Public check summary excludes source text, bodies, stdout, secrets and host paths."""
    summary = report.get("summary", {})
    counts = {key: int(value) for key, value in summary.items()
              if key in {"suspected_regression", "intentional_change", "intent_unresolved", "no_regression",
                         "flaky", "inconclusive", "invalid_baseline"}
              and isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 8}
    conclusion = ("failure" if counts.get("suspected_regression") else "action_required"
                  if any(counts.get(k) for k in ("intent_unresolved", "flaky", "inconclusive", "invalid_baseline"))
                  or not report.get("findings") or not sum(counts.values()) else "success")
    lines = ["Paired Docker execution; tool-free AI planning. Results are bounded evidence, not proof.", "",
             *[f"- {key.replace('_', ' ')}: {value}" for key, value in counts.items()], "",
             "Evidence references (inspect complete traces in your private BranchLab dashboard):"]
    budget = report.get("probe_budget", {})
    if budget.get("omitted", 0):
        lines.insert(1, f"Probe budget: {budget['selected']} of {budget['proposed']} proposed probes executed; "
                     f"{budget['omitted']} omitted. Inspect private report for budget reasons.")
    for finding in report.get("findings", [])[:8]:
        lines.append(f"- {_safe_text(finding.get('id', 'finding'), 64)}: "
                     f"{_safe_text(finding.get('classification', 'inconclusive'), 64)}")
        for citation in finding.get("citations", [])[:3]:
            lines.append(f"  - {_safe_text(citation.get('revision', ''), 4)} "
                         f"{_safe_text(citation.get('path', ''), 180)} "
                         f"lines {_safe_text(citation.get('line_start', ''), 8)}–"
                         f"{_safe_text(citation.get('line_end', ''), 8)}")
    return {"status": "completed", "conclusion": conclusion,
            "output": {"title": "BranchLab differential investigation", "summary": "\n".join(lines)[:20_000]}}


class AppWorker:
    """One bounded job per call. Run in a separate service, never in HTTP handlers."""

    def __init__(self, config: AppConfig, queue: JobQueue, *, client=None):
        self.config, self.queue = config, queue
        self.client = client or GitHubAppClient(config)

    def _mutate_check(self, method, path, token, body, job):
        # Always fence immediately before external mutation, including error paths.
        if not self.queue.heartbeat(job):
            raise AppError("Worker lease lost before Check Run mutation.")
        return self.client.request(method, path, token, body)

    def _validate_report(self, report, payload, report_id):
        if (not isinstance(report, dict) or report.get("id") != report_id
                or not re.fullmatch(r"run-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}", str(report_id))
                or report.get("runner") != "docker"
                or report.get("repository", {}).get("base_sha") != payload["base_sha"]
                or report.get("repository", {}).get("head_sha") != payload["head_sha"]
                or report.get("provider", {}).get("name") != self.config.planner_provider
                or report.get("provider", {}).get("live_ai") is not True):
            raise AppError("Investigation report identity, revisions or isolation do not match this job.")

    def _check(self, policy, token, job):
        if job["check_run_id"]:
            self._mutate_check("PATCH", f"/repos/{policy.full_name}/check-runs/{job['check_run_id']}", token,
                               {"status": "in_progress"}, job)
            return job["check_run_id"]
        external_id = f"branchlab-{job['job_id']}"
        # Recover creation after a crash between GitHub POST and the SQLite checkpoint.
        existing = self.client.request("GET", f"/repos/{policy.full_name}/commits/"
                                       f"{job['payload']['head_sha']}/check-runs?check_name=BranchLab&per_page=100", token)
        for check in existing.get("check_runs", []):
            if (check.get("external_id") == external_id and check.get("app", {}).get("id") == self.config.app_id
                    and _integer(check.get("id"))):
                self.queue.checkpoint(job, check_run_id=check["id"])
                self._mutate_check("PATCH", f"/repos/{policy.full_name}/check-runs/{check['id']}", token,
                                   {"status": "in_progress"}, job)
                return check["id"]
        result = self._mutate_check("POST", f"/repos/{policy.full_name}/check-runs", token,
                                    {"name": "BranchLab", "head_sha": job["payload"]["head_sha"],
                                     "status": "in_progress", "external_id": external_id}, job)
        if not _integer(result.get("id")):
            raise AppError("GitHub returned an invalid Check Run identifier.")
        self.queue.checkpoint(job, check_run_id=result["id"])
        return result["id"]

    def run_once(self) -> dict | None:
        from .engine import investigate
        from .gitops import diff_context
        from .models import Plan
        from .providers import build_plan, build_plan_openclaw

        job = self.queue.claim()
        if job is None:
            return None
        stop, lost = threading.Event(), threading.Event()

        def maintain():
            while not stop.wait(30):
                try:
                    if not self.queue.heartbeat(job):
                        lost.set()
                        return
                except Exception:
                    lost.set()
                    return

        thread = threading.Thread(target=maintain, daemon=True)
        thread.start()
        check_id, token, policy = None, None, None
        try:
            payload = job["payload"]
            policy = self.config.policy(payload["installation_id"], payload["repository_id"], payload["repository"])
            token = self.client.installation_token(policy)
            snapshot_dir = (self.config.state_dir / "snapshots" / f"job-{job['job_id']}" /
                            f"attempt-{job['attempts']}-{uuid.uuid4().hex[:8]}" / "repository")
            fetched = fetch_app_snapshot(self.client, policy, payload, snapshot_dir, token)
            check_id = self._check(policy, token, job)
            report = None
            if job["report_id"] and re.fullmatch(r"run-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}", job["report_id"]):
                report_path = self.config.report_dir / job["report_id"] / "report.json"
                if report_path.is_file() and not report_path.is_symlink() and report_path.stat().st_size <= 32_000_000:
                    report = json.loads(report_path.read_text())
                    self._validate_report(report, payload, job["report_id"])
            if report is None:
                context = diff_context(fetched["repo"], payload["base_sha"], payload["head_sha"])
                planner = build_plan if self.config.planner_provider == "openai" else build_plan_openclaw
                plan, provider = planner(context, fetched["description"], model=self.config.model)
                # Four probes with at most five HTTP steps bound repeated minimization work.
                probes = [p for p in plan.probes if len(p.steps) <= 5][:4]
                if (not probes or provider.get("name") != self.config.planner_provider
                        or provider.get("live_ai") is not True):
                    raise AppError("App investigations require a bounded live tool-free plan.")
                budget = {"proposed": len(plan.probes), "selected": len(probes),
                          "omitted": len(plan.probes) - len(probes),
                          "over_step_limit": sum(len(p.steps) > 5 for p in plan.probes),
                          "over_probe_limit": max(0, sum(len(p.steps) <= 5 for p in plan.probes) - 4),
                          "limits": {"probes": 4, "steps_per_probe": 5}}
                plan = Plan(intent_summary=plan.intent_summary, probes=probes)
                if lost.is_set():
                    raise AppError("Worker lease lost before execution.")
                report = investigate(fetched["repo"], payload["base_sha"], payload["head_sha"], plan,
                                     provider=provider, output=self.config.report_dir, app_entry=policy.app_entry,
                                     runner="docker", trusted_local=False, repeats=2, timeout=10)
                self._validate_report(report, payload, report.get("id"))
                report["probe_budget"] = budget
                if budget["omitted"]:
                    report.setdefault("limitations", []).append(
                        f"App execution budget selected {budget['selected']} of {budget['proposed']} proposed probes. "
                        f"Omitted {budget['over_step_limit']} exceeding five HTTP steps and "
                        f"{budget['over_probe_limit']} beyond the four-probe cap; omitted probes were not executed.")
                report_directory = self.config.report_dir / report["id"]
                # Replace persisted artifacts before checkpointing publication eligibility.
                from .engine import markdown_report
                (report_directory / "report.md").write_text(markdown_report(report))
                temporary = report_directory / "report.app.tmp"
                temporary.write_text(json.dumps(report, indent=2) + "\n")
                temporary.replace(report_directory / "report.json")
                self.queue.checkpoint(job, report_id=report["id"])
            current = self.client.read_pr(policy, payload["pr_number"], token)
            if current["draft"] or _snapshot(current, policy) != payload:
                raise SnapshotChanged("PR changed during investigation; stale check is not reported as current.")
            if lost.is_set() or not self.queue.heartbeat(job):
                raise AppError("Worker lease lost before reporting.")
            self._mutate_check("PATCH", f"/repos/{policy.full_name}/check-runs/{check_id}", token,
                               check_output(report), job)
            self.queue.finish(job, "completed")
        except Exception as exc:
            stale = isinstance(exc, SnapshotChanged)
            # Only our fixed messages are public. Never persist exception text from a provider/repository.
            error = (str(exc) if isinstance(exc, AppError) else
                     "Investigation failed; check App, model access, Docker image and repository compatibility.")
            if check_id and token and policy and not lost.is_set():
                try:
                    self._mutate_check("PATCH", f"/repos/{policy.full_name}/check-runs/{check_id}", token,
                                       {"status": "completed", "conclusion": "stale" if stale else "action_required",
                                        "output": {"title": "BranchLab investigation incomplete", "summary": error}}, job)
                except Exception:
                    error += " Check Run delivery remains unconfirmed."
            try:
                self.queue.finish(job, "skipped" if stale else "failed", error=error)
            except AppError:
                pass  # A new lease holder now owns the durable outcome.
        finally:
            stop.set()
            thread.join(timeout=2)
        return self.queue.get(job["job_id"])
