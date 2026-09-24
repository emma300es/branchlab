"""Read Git objects without checkout hooks, filters, or executing repository code."""

from __future__ import annotations

import ast
import difflib
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

MAX_FILE_BYTES = 256_000
MAX_CONTEXT_BYTES = 96_000
MAX_EXPORT_BYTES = 100_000_000
MAX_EXPORT_FILES = 5000
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_SECRET_NAME = re.compile(
    r"(^\.env($|\.)|(^|[._-])(secrets?|credentials?|tokens?|passwords?)([._-]|$)|"
    r"\.(pem|key|p12|pfx|keystore)$|^id_(rsa|dsa|ecdsa|ed25519)$)", re.I,
)
_SECRET_CONTENT = re.compile(
    r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|"
    r"(?:sk-(?:proj-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"AKIA[A-Z0-9]{16})|"
    r"(?:api[_-]?key|access[_-]?token|password|client[_-]?secret)\s*[=:]\s*['\"][^'\"\s]{12,}['\"]",
    re.I,
)


def _git(repo: Path, *args: str) -> bytes:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
         "-c", "core.attributesFile=/dev/null", "-C", str(repo), *args],
        capture_output=True, env=env, timeout=30,
    )
    if result.returncode:
        raise ValueError("Git revision could not be read (invalid repository, revision, or object).")
    return result.stdout


def resolve_revision(repo: Path, ref: str) -> str:
    """Resolve a user ref once to a full immutable commit ID; never accept options."""
    if not ref or ref.startswith("-") or len(ref) > 1024 or any(c in ref for c in "\r\n\0"):
        raise ValueError("Invalid revision")
    sha = _git(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()
    if not _SHA.fullmatch(sha):
        raise ValueError("Revision did not resolve to one commit")
    return sha


def _safe_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or "\\" in name or
            any(part in ("", ".", "..") for part in name.split("/")) or
            any(part.lower() == ".git" for part in path.parts) or
            any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise ValueError("Unsafe repository path")
    return path


def _tree(repo: Path, sha: str) -> dict[str, tuple[str, str, int]]:
    if not _SHA.fullmatch(sha):
        raise ValueError("Expected a full immutable commit hash")
    if resolve_revision(repo, sha) != sha:
        raise ValueError("Expected a commit hash")
    entries = {}
    total_size = 0
    for record in _git(repo, "ls-tree", "-r", "-l", "-z", sha).split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, kind, oid, size = metadata.decode("ascii").split()
        name = raw_path.decode("utf-8", errors="strict")
        _safe_path(name)
        if kind != "blob" or mode not in ("100644", "100755"):
            raise ValueError("Symlinks and submodules are not supported in snapshots")
        length = int(size)
        total_size += length
        entries[name] = (mode, oid, length)
        if len(entries) > MAX_EXPORT_FILES or total_size > MAX_EXPORT_BYTES:
            raise ValueError("Repository snapshot exceeds size limits")
    return entries


def export_revision(repo: Path, sha: str, destination: Path) -> None:
    """Export checked regular Git blobs into a new/empty directory, without Git metadata."""
    entries = _tree(repo, sha)  # Validate the entire tree before writing any files.
    destination = Path(os.path.abspath(destination))
    if destination.resolve() != destination:
        raise ValueError("Snapshot destination must not use symlinks")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("Snapshot destination must be new or empty")
    destination.mkdir(parents=True, exist_ok=True)
    for name, (mode, oid, _) in entries.items():
        target = destination.joinpath(*_safe_path(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.parent.resolve().is_relative_to(destination) is False:
            raise ValueError("Snapshot path escaped destination")
        data = _git(repo, "cat-file", "blob", oid)
        with target.open("xb") as stream:
            stream.write(data)
        target.chmod(0o755 if mode == "100755" else 0o644)


def _text(repo: Path, name: str, entry: tuple[str, str, int] | None) -> str | None:
    if entry is None:
        return ""
    if entry[2] > MAX_FILE_BYTES or any(_SECRET_NAME.search(p) for p in PurePosixPath(name).parts):
        return None
    content = _git(repo, "cat-file", "blob", entry[1])
    if b"\0" in content:
        return None
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if _SECRET_CONTENT.search(text):
        return None
    return text


def _impact_graph(files: dict, changed: list[str]) -> dict:
    """A static, explicitly limited Python import graph, not inferred runtime causality."""
    modules = {}
    for path in files:
        if path.endswith(".py"):
            module = path[:-3].replace("/", ".")
            if module.endswith(".__init__"):
                module = module[:-9]
            modules[module] = path
            if module.startswith("src."):
                modules[module[4:]] = path
    edges = set()
    for path, versions in files.items():
        if not path.endswith(".py"):
            continue
        # Include both sides so removed imports remain visible as possible impact.
        for source in (versions["base"], versions["head"]):
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            package = path[:-3].replace("/", ".").split(".")
            package = package[:-1]
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        prefix = package[:max(0, len(package) - node.level + 1)]
                        module = ".".join(prefix + ([node.module] if node.module else []))
                    else:
                        module = node.module or ""
                    names = [module] + [".".join(filter(None, (module, a.name))) for a in node.names]
                for name in names:
                    while name:
                        target = modules.get(name)
                        if target:
                            if target != path:
                                edges.add((path, target))
                            break
                        name = name.rpartition(".")[0]
    return {
        "nodes": [{"id": p, "label": p, "kind": "file", "changed": p in changed} for p in sorted(files)],
        "edges": [{"source": a, "target": b, "kind": "imports"} for a, b in sorted(edges)],
    }


def diff_context(repo: Path, base_sha: str, head_sha: str) -> dict:
    base, head = _tree(repo, base_sha), _tree(repo, head_sha)
    changed = sorted(p for p in base.keys() | head.keys() if base.get(p) != head.get(p))
    files, omitted = {}, []
    used = 0
    # Changed files first, then contracts/docs and unchanged Python dependency evidence.
    unchanged = (base.keys() | head.keys()) - set(changed)
    docs = sorted(p for p in unchanged if p not in changed and p.lower().endswith((".md", ".rst", ".txt")))
    modules = sorted(p for p in unchanged if p not in changed and p.endswith(".py"))
    candidates = changed + docs + modules
    omitted.extend(candidates[250:])
    for name in candidates[:250]:
        candidate_size = sum(tree.get(name, (None, None, 0))[2] for tree in (base, head))
        if used + candidate_size > MAX_CONTEXT_BYTES:
            omitted.append(name)
            continue
        before, after = _text(repo, name, base.get(name)), _text(repo, name, head.get(name))
        if before is None or after is None:
            omitted.append(name)
            continue
        size = len(before.encode()) + len(after.encode())
        if used + size > MAX_CONTEXT_BYTES:
            omitted.append(name)
            continue
        files[name] = {"base": before, "head": after}
        used += size
    diff = "".join(
        line for name in changed if name in files
        for line in difflib.unified_diff(
            files[name]["base"].splitlines(keepends=True), files[name]["head"].splitlines(keepends=True),
            fromfile=f"base/{name}", tofile=f"head/{name}", n=3,
        )
    )
    return {
        "diff": diff[:MAX_CONTEXT_BYTES], "changed_files": changed, "files": files,
        "impact_graph": _impact_graph(files, changed), "omitted_files": omitted,
        "limitations": ["Static Python imports only; dynamic imports and runtime dependencies are not inferred.",
                        "Secret-pattern, binary, large and over-budget file contents are omitted; this is not a secret scanner."],
    }
