import json
import time

import pytest

from branchlab.models import Probe
from branchlab.runner import run_probe


def probe(expected=200, *, target="status", path="", operator="eq"):
    return Probe.model_validate({
        "id": "health", "title": "Health", "invariant": "health works", "intent": "preserve",
        "severity": "low", "rationale": "test", "citations": [],
        "steps": [{"id": "read", "method": "GET", "path": "/", "headers": [], "body_json": None}],
        "assertions": [{"step_id": "read", "target": target, "path": path,
                        "operator": operator, "expected_json": json.dumps(expected)}],
    })


def source(tmp_path, body):
    directory = tmp_path / "source"
    directory.mkdir()
    (directory / "service.py").write_text(body)
    return directory


APP = 'from fastapi import FastAPI\napp=FastAPI()\n@app.get("/")\ndef root():\n    return {"ok": True}\n'


def test_subprocess_needs_explicit_trust(tmp_path):
    directory = source(tmp_path, APP)
    with pytest.raises(ValueError, match="trusted_local"):
        run_probe(directory, "service:app", probe(), runner="subprocess")


def test_pass_fail_and_missing_json_are_assertions(tmp_path):
    directory = source(tmp_path, APP)
    passed = run_probe(directory, "service:app", probe(), runner="subprocess", trusted_local=True)
    assert passed["outcome"] == "passed", passed
    assert passed["checks"][0]["observed"] == 200
    assert passed["environment"]["security_sandbox"] is False
    failed = run_probe(directory, "service:app", probe(201), runner="subprocess", trusted_local=True)
    assert failed["outcome"] == "failed", failed
    missing = run_probe(directory, "service:app", probe("anything", target="json", path="missing", operator="ne"),
                        runner="subprocess", trusted_local=True)
    assert missing["outcome"] == "failed"
    assert missing["checks"][0]["observed"] == {"missing": True}
    boolean = run_probe(directory, "service:app", probe(1, target="json", path="ok"),
                        runner="subprocess", trusted_local=True)
    assert boolean["outcome"] == "failed", "JSON true must not compare equal to numeric 1"


@pytest.mark.parametrize("code", ["raise ImportError('dependency absent')", APP.replace('return {"ok": True}', "raise RuntimeError('broken')")])
def test_import_or_handler_errors_are_not_assertion_failures(tmp_path, code):
    directory = source(tmp_path, code)
    result = run_probe(directory, "service:app", probe(), runner="subprocess", trusted_local=True)
    assert result["outcome"] == "error", result
    assert result["error"]["type"] in ("ImportError", "RuntimeError")


def test_environment_has_no_host_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-not-for-child")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "other-test-secret")
    monkeypatch.setenv("HTTPS_PROXY", "https://credential-proxy.invalid")
    monkeypatch.setenv("PYTHONPATH", "/untrusted/path")
    directory = source(tmp_path, 'import os\n' + APP.replace('return {"ok": True}',
                        'return {"present": any(k in os.environ for k in ["OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "HTTPS_PROXY", "PYTHONPATH"])}'))
    result = run_probe(directory, "service:app", probe(False, target="json", path="present"),
                       runner="subprocess", trusted_local=True)
    assert result["outcome"] == "passed", result
    assert "test-secret" not in json.dumps(result)


def test_timeout_kills_child_process_group(tmp_path):
    marker = tmp_path / "child-survived"
    child_code = f"import time; from pathlib import Path; time.sleep(2); Path({str(marker)!r}).write_text('survived')"
    code = ("import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
            "time.sleep(30)\n" + APP)
    directory = source(tmp_path, code)
    result = run_probe(directory, "service:app", probe(), runner="subprocess", timeout=1, trusted_local=True)
    assert result["outcome"] == "timeout", result
    time.sleep(2)
    assert not marker.exists(), "runaway descendant remained alive after timeout"


def test_output_is_bounded_and_fresh_runs(tmp_path):
    directory = source(tmp_path, APP.replace('return {"ok": True}', 'print("x" * 100_000)\n    return {"ok": True}'))
    result = run_probe(directory, "service:app", probe(), runner="subprocess", trusted_local=True)
    assert result["outcome"] == "passed", result
    assert len(result["stdout"]) <= 16_000


def test_default_docker_never_falls_back(tmp_path, monkeypatch):
    directory = source(tmp_path, "raise RuntimeError('MUST NOT EXECUTE')")
    monkeypatch.setattr("branchlab.runner.shutil.which", lambda name: None)
    result = run_probe(directory, "service:app", probe())
    assert result["outcome"] == "error"
    assert "Docker is unavailable" in result["stderr"]
    assert "MUST NOT EXECUTE" not in json.dumps(result)


def test_source_symlink_rejected(tmp_path):
    directory = source(tmp_path, APP)
    (directory / "external.py").symlink_to(tmp_path / "outside.py")
    with pytest.raises(ValueError, match="symlink"):
        run_probe(directory, "service:app", probe(), runner="subprocess", trusted_local=True)


def test_docker_command_has_isolation_flags(tmp_path, monkeypatch):
    directory = source(tmp_path, APP)
    commands = []
    monkeypatch.setattr("branchlab.runner.shutil.which", lambda name: "/fake/docker")

    class FakeProcess:
        pid = 999_999_999
        returncode = 1

        def __init__(self, command, **kwargs):
            commands.append((command, kwargs))

        def wait(self, timeout=None):
            return 1

    monkeypatch.setattr("branchlab.runner.subprocess.Popen", FakeProcess)
    monkeypatch.setattr("branchlab.runner.subprocess.run", lambda *a, **kw: None)
    result = run_probe(directory, "service:app", probe())
    assert result["outcome"] == "error"
    command, kwargs = commands[0]
    assert all(flag in command for flag in ("--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--user=65532:65532", "--memory=512m", "--pids-limit=96"))
    assert command.count("--mount") == 3
    assert "readonly" in next(arg for arg in command if "dst=/snapshot" in arg)
    assert kwargs["start_new_session"] is True
    assert set(kwargs["env"]) == {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "PYTHONIOENCODING", "PYTHONDONTWRITEBYTECODE"}
