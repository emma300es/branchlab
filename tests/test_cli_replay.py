import json
from pathlib import Path
import shlex

from branchlab import cli
from branchlab.demo import create_demo_repo, demo_plan
from branchlab.engine import investigate


def test_cli_freezes_planning_shas_before_execute(monkeypatch, tmp_path):
    plan = demo_plan()
    captured = {}
    monkeypatch.setattr("branchlab.gitops.resolve_revision", lambda repo, ref: {"base": "a" * 40, "head": "b" * 40}[ref])
    monkeypatch.setattr("branchlab.gitops.diff_context", lambda *args: {"files": {}})
    monkeypatch.setattr(cli, "plan_for", lambda *args: (plan, {"live_ai": False}))
    monkeypatch.setattr(cli, "emit", lambda report: None)

    def execute(repo, base, head, plan, **kwargs):
        captured.update(base=base, head=head)
        return {"id": "run-test"}

    monkeypatch.setattr("branchlab.engine.investigate", execute)
    cli.investigate(tmp_path, base="base", head="head", output=tmp_path)
    assert captured == {"base": "a" * 40, "head": "b" * 40}


def test_replay_uses_saved_sha_timeout_and_never_reminimizes(monkeypatch, tmp_path):
    plan = demo_plan()
    report_file = tmp_path / "report.json"
    report_file.write_text(json.dumps({
        "repository": {"base_sha": "a" * 40, "head_sha": "b" * 40},
        "intent_summary": plan.intent_summary, "findings": [p.model_dump() for p in plan.probes],
        "timeout": 73, "repeats": 3, "app_entry": "myapp:app",
    }))
    captured = {}
    monkeypatch.setattr(cli, "emit", lambda report: None)

    def execute(repo, base, head, plan, **kwargs):
        captured.update(base=base, head=head, plan=plan, **kwargs)
        return {"id": "run-replayed"}

    monkeypatch.setattr("branchlab.engine.investigate", execute)
    cli.replay(str(report_file), tmp_path, case="tenant-isolation", output=tmp_path / "custom")
    assert captured["base"] == "a" * 40
    assert captured["head"] == "b" * 40
    assert captured["timeout"] == 73
    assert captured["repeats"] == 3
    assert captured["minimize_cases"] is False
    assert captured["app_entry"] == "myapp:app"
    assert [p.id for p in captured["plan"].probes] == ["tenant-isolation"]


def test_custom_output_reproducer_is_shell_quoted_and_usable(monkeypatch, tmp_path):
    repo = create_demo_repo(tmp_path / "repo with spaces")
    output = tmp_path / "custom output"
    plan = demo_plan()
    monkeypatch.setattr("branchlab.engine._paired", lambda *args: ([{"outcome": "passed"}] * 2,
                                                                  [{"outcome": "passed"}] * 2))
    report = investigate(repo, "base", "head", plan, output=output,
                         provider={"name": "fixture", "live_ai": False}, timeout=41)
    command = shlex.split(report["reproduce_command"])
    assert command[:2] == ["branchlab", "replay"]
    assert Path(command[2]).is_file()
    assert command[command.index("--repo") + 1] == str(repo)
    assert command[command.index("--output") + 1] == str(output)
    assert report["timeout"] == 41


def test_default_demo_keeps_repository_for_replay(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(cli, "emit", lambda report: None)

    def execute(repo, *args, **kwargs):
        seen["repo"] = repo
        return {"id": "run-demo"}

    monkeypatch.setattr("branchlab.engine.investigate", execute)
    cli.demo(output=tmp_path / "runs")
    assert seen["repo"].parent == tmp_path / "fixtures"
    assert (seen["repo"] / ".git").is_dir()


def test_replay_cli_accepts_generated_repo_option(monkeypatch, tmp_path):
    from typer.testing import CliRunner
    monkeypatch.setattr(cli, "emit", lambda report: None)
    monkeypatch.setattr("branchlab.engine.investigate", lambda *args, **kwargs: {"id": "test"})
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"repository": {"base_sha": "a" * 40, "head_sha": "b" * 40},
                               "intent_summary": "test", "findings": [p.model_dump() for p in demo_plan().probes]}))
    result = CliRunner().invoke(cli.app, ["replay", str(path), "--repo", str(tmp_path),
                                        "--runner", "subprocess", "--trust-local-code"])
    assert result.exit_code == 0, result.output
