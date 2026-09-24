"""One fresh-process declarative HTTP probe. Not a security boundary by itself."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import importlib
import io
import json
import os
from pathlib import Path
import re
import resource
import sys
import time

MAX_TEXT = 16_000
MAX_BODY = 32_000
_MISSING = object()


class BoundedWriter(io.TextIOBase):
    def __init__(self, limit=MAX_TEXT):
        self.limit = limit
        self.parts = []
        self.size = 0
        self.truncated = False

    def write(self, value):
        value = str(value)
        remaining = max(0, self.limit - self.size)
        self.parts.append(value[:remaining]) if remaining else None
        self.size += min(len(value), remaining)
        self.truncated = self.truncated or len(value) > remaining
        return len(value)

    def getvalue(self):
        return "".join(self.parts) + ("\n[output truncated]" if self.truncated else "")


def _bounded(value, limit=MAX_BODY):
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > limit:
        return {"truncated": True, "preview": text[:limit]}
    return value


def _lookup(body, path):
    if path in ("", "$", "."):
        return body
    if path.startswith("/"):
        parts = [p.replace("~1", "/").replace("~0", "~") for p in path[1:].split("/")]
    else:
        path = path.removeprefix("$.")
        parts = re.sub(r"\[(\d+)\]", r".\1", path).split(".")
    value = body
    for part in parts:
        try:
            if isinstance(value, list):
                if not part.isdigit():
                    return _MISSING
                value = value[int(part)]
            elif isinstance(value, dict):
                value = value[part]
            else:
                return _MISSING
        except (KeyError, IndexError, TypeError):
            return _MISSING
    return value


def _json_equal(left, right):
    # JSON booleans are not numbers; Python otherwise considers True == 1.
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_json_equal(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    return left == right


def _limits():
    # Defense in depth for accidental runaway code, not isolation from malicious code.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (8_000_000, 8_000_000))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    resource.setrlimit(resource.RLIMIT_CPU, (20, 21))
    resource.setrlimit(resource.RLIMIT_AS, (1_073_741_824, 1_073_741_824))


def execute(source: Path, app_entry: str, probe: dict) -> dict:
    started = time.monotonic()
    output, errors = BoundedWriter(), BoundedWriter()
    result = {"outcome": "error", "checks": [], "responses": [], "stdout": "", "stderr": ""}
    with redirect_stdout(output), redirect_stderr(errors):
        try:
            from fastapi.testclient import TestClient
            if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", app_entry):
                raise ValueError("App entry must be a module:attribute")
            module_name, attribute = app_entry.split(":")
            sys.path.insert(0, str(source.resolve()))
            application = getattr(importlib.import_module(module_name), attribute)
            responses = {}
            with TestClient(application, raise_server_exceptions=True) as client:
                for step in probe["steps"]:
                    path = step["path"]
                    if not path.startswith("/") or path.startswith("//") or "://" in path:
                        raise ValueError("HTTP step must use a local relative path")
                    response = client.request(
                        step["method"], path,
                        headers={h["name"]: h["value"] for h in step.get("headers", [])},
                        json=json.loads(step["body_json"]) if step.get("body_json") is not None else None,
                        follow_redirects=False,
                    )
                    try:
                        body = response.json()
                    except ValueError:
                        body = _MISSING
                    responses[step["id"]] = (response.status_code, body)
                    result["responses"].append({
                        "step_id": step["id"], "method": step["method"], "path": path,
                        "status": response.status_code,
                        "body": response.text[:MAX_BODY] if body is _MISSING else _bounded(body),
                    })
                for assertion in probe["assertions"]:
                    status, body = responses[assertion["step_id"]]
                    observed = status if assertion["target"] == "status" else _lookup(body, assertion["path"])
                    expected = json.loads(assertion["expected_json"])
                    # Absent JSON/path is a failed observable check, never a passing 'ne'.
                    passed = observed is not _MISSING and (
                        _json_equal(observed, expected) if assertion["operator"] == "eq" else not _json_equal(observed, expected)
                    )
                    result["checks"].append({
                        "step_id": assertion["step_id"], "target": assertion["target"],
                        "path": assertion["path"], "operator": assertion["operator"],
                        "expected": _bounded(expected),
                        "observed": {"missing": True} if observed is _MISSING else _bounded(observed),
                        "passed": bool(passed),
                    })
            result["outcome"] = "passed" if all(c["passed"] for c in result["checks"]) else "failed"
        except BaseException as exc:
            # Import/runtime/lifespan/SystemExit errors are not invariant failures.
            result["outcome"] = "error"
            result["error"] = {"type": type(exc).__name__, "message": str(exc)[:2000]}
    result["stdout"], result["stderr"] = output.getvalue(), errors.getvalue()
    result["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--app", required=True)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    _limits()
    probe = json.loads(args.probe.read_text())
    result = execute(args.source, args.app, probe)
    temporary = args.result.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False))
    os.replace(temporary, args.result)


if __name__ == "__main__":
    main()
