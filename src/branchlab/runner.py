"""Bounded fresh-run execution: Docker by default, explicitly trusted subprocess opt-in."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

from .models import Probe

MAX_OUTPUT = 16_000
MAX_RESULT_BYTES = 2_000_000


def _environment(kind: str) -> dict:
    return {
        "runner": kind, "fresh_process": True, "host_credentials_inherited": False,
        "network": "none" if kind == "docker" else "not isolated; probe HTTP uses TestClient",
        "source_read_only": kind == "docker", "security_sandbox": kind == "docker",
        "warning": None if kind == "docker" else "Trusted local code only. Subprocess is NOT a security sandbox.",
    }


def _clean_env(work: Path) -> dict:
    # Deliberate allowlist: no host API keys, tokens, proxy settings, Python injection or login config.
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(work), "TMPDIR": str(work),
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _read_limited(path: Path, limit: int = MAX_OUTPUT) -> str:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    return data[:limit].decode("utf-8", errors="replace") + ("\n[output truncated]" if len(data) > limit else "")


def run_probe(source_dir: Path, app_entry: str, probe: Probe, *, runner: str = "docker",
              timeout: int = 20, trusted_local: bool = False) -> dict:
    if runner not in ("docker", "subprocess"):
        raise ValueError("Runner must be docker or subprocess")
    if runner == "subprocess" and not trusted_local:
        raise ValueError("Subprocess runs require explicit trusted_local=True; repository code executes on your host")
    if not 0 < timeout <= 300:
        raise ValueError("Timeout must be between 0 and 300 seconds")
    if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", app_entry):
        raise ValueError("App entry must be a module:attribute")
    source_dir = source_dir.resolve(strict=True)
    if not source_dir.is_dir():
        raise ValueError("Source snapshot must be a directory")
    for path in source_dir.rglob("*"):
        if path.is_symlink():
            raise ValueError("Source snapshot may not contain symlinks")
    started = time.monotonic()
    environment = _environment(runner)
    result = {"outcome": "error", "duration_ms": 0, "checks": [], "responses": [],
              "stdout": "", "stderr": "", "environment": environment}
    docker = shutil.which("docker") if runner == "docker" else None
    if runner == "docker" and docker is None:
        result["stderr"] = "Docker is unavailable. Install Docker and build docker/runner.Dockerfile; no unsafe fallback was used."
        return result
    with tempfile.TemporaryDirectory(prefix="branchlab-probe-") as temp:
        work = Path(temp)
        input_dir, output_dir = work / "input", work / "output"
        input_dir.mkdir()
        output_dir.mkdir()
        probe_file, result_file = input_dir / "probe.json", output_dir / "result.json"
        probe_file.write_text(probe.model_dump_json())
        container = "branchlab-" + uuid.uuid4().hex
        if runner == "docker":
            # Container uid owns only the result mount. Source and probe are mounted read-only.
            output_dir.chmod(0o777)
            command = [
                str(docker), "run", "--rm", "--pull=never", "--name", container,
                "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--user=65532:65532", "--pids-limit=96", "--memory=512m", "--cpus=1",
                "--ulimit", "nofile=128:128", "--ulimit", "fsize=8000000:8000000",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                "--mount", f"type=bind,src={source_dir},dst=/snapshot,readonly",
                "--mount", f"type=bind,src={input_dir},dst=/input,readonly",
                "--mount", f"type=bind,src={output_dir},dst=/output",
                "branchlab-runner:0.1", "--source", "/snapshot", "--app", app_entry,
                "--probe", "/input/probe.json", "--result", "/output/result.json",
            ]
        else:
            command = [sys.executable, "-I", "-B", str(Path(__file__).with_name("worker.py")),
                       "--source", str(source_dir), "--app", app_entry,
                       "--probe", str(probe_file), "--result", str(result_file)]
        stdout_path, stderr_path = work / "stdout", work / "stderr"
        timed_out = False
        try:
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process = subprocess.Popen(command, cwd=work, env=_clean_env(work), stdout=stdout,
                                           stderr=stderr, start_new_session=True)
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                finally:
                    # Also reap descendants left by an otherwise-finished trusted worker.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
            result["stdout"] = _read_limited(stdout_path)
            result["stderr"] = _read_limited(stderr_path)
            if timed_out:
                result["outcome"] = "timeout"
                result["stderr"] += "\nProbe exceeded its wall-clock timeout."
            elif process.returncode != 0:
                result["stderr"] += f"\nWorker exited with code {process.returncode}; this is not an assertion failure."
            elif result_file.is_file() and not result_file.is_symlink() and result_file.stat().st_size <= MAX_RESULT_BYTES:
                data = json.loads(result_file.read_text())
                if data.get("outcome") not in ("passed", "failed", "error") or not isinstance(data.get("checks"), list):
                    raise ValueError("Invalid worker result")
                # A purported pass without actual checks is not evidence.
                if data["outcome"] in ("passed", "failed") and not data["checks"]:
                    raise ValueError("Worker returned no assertion checks")
                result.update(data)
                result["stdout"] = result.get("stdout", "")[:MAX_OUTPUT]
                result["stderr"] = result.get("stderr", "")[:MAX_OUTPUT]
                result["environment"] = environment
            else:
                result["stderr"] += "\nWorker produced no bounded result file."
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            result["outcome"] = "error"
            result["stderr"] = (result["stderr"] + f"\nRunner error: {type(exc).__name__}: {exc}")[:MAX_OUTPUT]
        finally:
            if runner == "docker":
                # Docker CLI termination alone does not reliably stop the daemon's container.
                try:
                    subprocess.run([str(docker), "rm", "--force", container], env=_clean_env(work),
                                   capture_output=True, timeout=8)
                except (OSError, subprocess.TimeoutExpired):
                    result["stderr"] += "\nContainer cleanup could not be confirmed."
    result["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
    return result
