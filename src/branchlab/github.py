"""Read-only GitHub PR snapshots. Never comment, push, or execute repository code."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit


class GitHubError(RuntimeError):
    """Safe-to-display ingestion errors without raw credential-bearing stderr."""


def _parse_url(url: str) -> tuple[str, str, int]:
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise GitHubError("Expected https://github.com/OWNER/REPO/pull/NUMBER.") from exc
    match = re.fullmatch(r"/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_.-]{1,100})/pull/([1-9][0-9]*)/?", parsed.path)
    if (
        parsed.scheme != "https" or parsed.netloc != "github.com"
        or parsed.query or parsed.fragment or not match
        or match.group(2) in (".", "..")
    ):
        raise GitHubError("Expected https://github.com/OWNER/REPO/pull/NUMBER without extra URL components.")
    return match.group(1), match.group(2), int(match.group(3))


def _run(args: list[str], *, env: dict | None = None, timeout: int = 120) -> str:
    try:
        result = subprocess.run(
            args, check=False, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
    except FileNotFoundError as exc:
        raise GitHubError(f"Required executable {args[0]} is not installed.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitHubError(f"{args[0]} timed out; the snapshot is incomplete.") from exc
    if result.returncode:
        raise GitHubError(f"{args[0]} failed (exit {result.returncode}); check GitHub access and connectivity.")
    if len(result.stdout) > 4_000_000:
        raise GitHubError("GitHub response exceeded the ingestion size limit.")
    return result.stdout


def _read_pr(owner: str, repository: str, number: int) -> dict:
    raw = _run(["gh", "api", "--hostname", "github.com", f"repos/{owner}/{repository}/pulls/{number}"], timeout=45)
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("number") != number:
            raise ValueError("unexpected PR")
        if value["base"]["repo"]["full_name"].lower() != f"{owner}/{repository}".lower():
            raise ValueError("unexpected repository")
        for name in ("base", "head"):
            if not re.fullmatch(r"[a-f0-9]{40}", value[name]["sha"]):
                raise ValueError("invalid SHA")
        if not isinstance(value.get("title"), str) or not isinstance(value.get("body") or "", str):
            raise ValueError("invalid description")
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise GitHubError("GitHub returned malformed or mismatched PR metadata.") from exc
    return value


def _snapshot_key(pr: dict) -> tuple:
    return (
        pr["base"]["sha"], pr["head"]["sha"],
        (pr["base"].get("repo") or {}).get("id"),
        (pr["head"].get("repo") or {}).get("id"),
        pr["title"], pr.get("body") or "",
    )


def _destination(path: Path) -> Path:
    if ".." in path.parts:
        raise GitHubError("Destination traversal segments are not allowed.")
    target = path.absolute()
    for ancestor in (target, *target.parents):
        if ancestor.is_symlink():
            raise GitHubError("Destination and parent directories must not be symlinks.")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise GitHubError("Destination must be a new or empty directory; existing contents are preserved.")
    return target


def fetch_pr(url: str, destination: Path) -> dict:
    """Fetch a PR as immutable Git objects, verifying no update occurred mid-fetch.

    Authentication is delegated to the installed ``gh`` credential helper. No
    tokens are read, printed, or saved by BranchLab. The checkout remains empty;
    downstream export reads only the captured commit SHAs, not a moving branch.
    On any failure the partial repository is left for inspection, never deleted.
    """
    owner, repository, number = _parse_url(url)
    target = _destination(Path(destination))
    before = _read_pr(owner, repository, number)
    base_sha, head_sha = before["base"]["sha"], before["head"]["sha"]
    # Do not load host Git URL rewrites, hooks or transport settings. Keep gh's
    # existing authentication environment without exposing credentials in argv.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull, "GIT_TERMINAL_PROMPT": "0",
    })
    git = [
        "git", "-c", f"core.hooksPath={os.devnull}",
        "-c", "protocol.file.allow=never", "-c", "protocol.ext.allow=never",
        "-c", "credential.interactive=never", "-c", "credential.https://github.com.helper=",
        "-c", "credential.https://github.com.helper=!gh auth git-credential",
    ]
    target.parent.mkdir(parents=True, exist_ok=True)
    _destination(target)  # Recheck after creating ancestors, before mutation.
    _run(git + [
        "clone", "--no-checkout", "--no-recurse-submodules", "--filter=blob:none",
        "--", f"https://github.com/{owner}/{repository}.git", str(target),
    ], env=env)
    _run(git + ["-C", str(target), "fetch", "--no-tags", "origin", base_sha, head_sha], env=env)
    for sha in (base_sha, head_sha):
        fetched = _run(git + ["-C", str(target), "rev-parse", "--verify", f"{sha}^{{commit}}"], env=env).strip()
        if fetched != sha:
            raise GitHubError("Fetched Git object does not match the immutable PR revision.")
    after = _read_pr(owner, repository, number)
    if _snapshot_key(before) != _snapshot_key(after):
        raise GitHubError("PR changed during fetch; snapshot rejected. Retry with a new empty destination.")
    return {
        "repo_path": str(target), "base_ref": base_sha, "head_ref": head_sha,
        "pr_description": f"{before['title']}\n\n{before.get('body') or ''}",
        "url": f"https://github.com/{owner}/{repository}/pull/{number}",
    }
