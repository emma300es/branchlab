import json
import subprocess

import pytest

from branchlab.demo import demo_plan
from branchlab.providers import PlanError, build_plan_openclaw


def fake_process(monkeypatch, payload, returncode=0):
    calls = []

    class Process:
        pid = 12345

        def __init__(self, args, **kwargs):
            self.returncode = returncode
            calls.append((args, kwargs))
            kwargs["stdout"].write(json.dumps(payload).encode())

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr("branchlab.providers.subprocess.Popen", Process)
    return calls


def test_openclaw_supported_infer_no_agent_turn(monkeypatch):
    calls = fake_process(monkeypatch, {"ok": True, "provider": "openai", "model": "gpt-6-astra",
                                      "outputs": [{"text": demo_plan().model_dump_json(), "mediaUrl": None}]})
    plan, evidence = build_plan_openclaw({"files": {}, "private_caller_key": "not-for-model"}, "untrusted PR")
    assert plan == demo_plan()
    args = calls[0][0]
    assert args[:6] == ["openclaw", "infer", "model", "run", "--local", "--agent"]
    assert "not-for-model" not in args[args.index("--prompt") + 1]
    assert "untrusted PR" in args[args.index("--prompt") + 1]
    assert evidence["live_ai"] is True
    assert evidence["input_tokens"] is None and evidence["output_tokens"] is None
    assert "Local Pydantic" in evidence["schema_enforcement"]


@pytest.mark.parametrize("response", [
    {"ok": False, "outputs": []},
    {"ok": True, "outputs": []},
    {"ok": True, "outputs": [{"text": "```json\n{}\n```"}]},
    {"ok": True, "outputs": [{"text": "{\"intent_summary\": \"truncated"}]},
    {"ok": True, "outputs": [{"text": "{}"}]},
    {"ok": True, "outputs": [{"text": "{}"}, {"text": "{}"}]},
    {"ok": True, "outputs": [{"mediaUrl": "https://example.org/a.png"}]},
])
def test_unusable_model_results_rejected(monkeypatch, response):
    fake_process(monkeypatch, response)
    with pytest.raises(PlanError):
        build_plan_openclaw({}, "")


def test_failed_cli_does_not_leak_output(monkeypatch):
    fake_process(monkeypatch, {"error": "sensitive-provider-details"}, returncode=1)
    with pytest.raises(PlanError) as failure:
        build_plan_openclaw({}, "")
    assert "sensitive-provider-details" not in str(failure.value)


def test_prompt_bound_checked_before_spawn(monkeypatch):
    calls = fake_process(monkeypatch, {})
    with pytest.raises(PlanError, match="110 KB"):
        build_plan_openclaw({}, "x" * 110_001)
    assert not calls


def test_timeout_kills_process_group(monkeypatch):
    killed = []

    class Process:
        pid = 12345
        waits = 0

        def __init__(self, *args, **kwargs):
            pass

        def wait(self, timeout=None):
            if timeout:
                raise subprocess.TimeoutExpired("openclaw", timeout)

    monkeypatch.setattr("branchlab.providers.subprocess.Popen", Process)
    monkeypatch.setattr("branchlab.providers.os.killpg", lambda pid, signal: killed.append(pid))
    with pytest.raises(PlanError, match="timed out"):
        build_plan_openclaw({}, "")
    assert killed == [12345]
