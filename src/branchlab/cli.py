"""BranchLab command line: inspect, investigate, replay, demo and serve."""

import json
import uuid
from pathlib import Path
from typing import Annotated

import typer

from . import __version__
from .models import Plan

app = typer.Typer(no_args_is_help=True, help="Investigate pull-request regressions with reproducible evidence.")
DEFAULT_OUTPUT = Path(".branchlab/runs")


def emit(report):
    typer.echo(json.dumps({"id": report["id"], "summary": report["summary"],
                           "provider": report["provider"], "duration_ms": report["duration_ms"]}, indent=2))


def plan_for(context, description, plan_file, provider, model, trusted_local):
    if plan_file:
        return Plan.model_validate_json(plan_file.read_text()), {
            "name": "supplied-plan", "model": "user-supplied", "live_ai": False,
            "input_tokens": 0, "output_tokens": 0}
    if provider == "openai":
        from .providers import build_plan
        return build_plan(context, description, model=model)
    if provider == "codex":
        from .providers import build_plan_codex
        return build_plan_codex(context, description, model=model, trusted_context=trusted_local)
    raise ValueError("provider must be openai or codex (or supply --plan)")


@app.command()
def inspect(repo: Path, base: str = "main", head: str = "HEAD"):
    """Read-only impact context, without model calls or executing repository code."""
    from .gitops import diff_context, resolve_revision
    context = diff_context(repo, resolve_revision(repo, base), resolve_revision(repo, head))
    typer.echo(json.dumps(context, indent=2))


@app.command()
def investigate(
    repo: Path,
    base: str = "main",
    head: str = "HEAD",
    app_entry: Annotated[str, typer.Option("--app")] = "service:app",
    description: str = "",
    description_file: Path | None = None,
    plan: Path | None = None,
    provider: str = "openai",
    model: str | None = None,
    runner: str = "docker",
    trust_local_code: bool = False,
    repeats: int = 2,
    timeout: int = 20,
    output: Path = DEFAULT_OUTPUT,
):
    """Generate a declarative plan (or load one), then compare immutable Git revisions."""
    from .engine import investigate as execute
    from .gitops import diff_context, resolve_revision
    base_sha, head_sha = resolve_revision(repo, base), resolve_revision(repo, head)
    context = diff_context(repo, base_sha, head_sha)
    if description_file:
        description = description_file.read_text()
    probe_plan, provenance = plan_for(context, description, plan, provider, model, trust_local_code)
    report = execute(repo, base_sha, head_sha, probe_plan, provider=provenance, output=output, app_entry=app_entry,
                     runner=runner, trusted_local=trust_local_code, repeats=repeats, timeout=timeout)
    emit(report)
    typer.echo(f"Evidence: {output / report['id']}")


@app.command()
def pr(url: str, destination: Path, app_entry: Annotated[str, typer.Option("--app")] = "service:app",
       provider: str = "openai", model: str | None = None, output: Path = DEFAULT_OUTPUT,
       runner: str = "docker", trust_local_code: bool = False, plan: Path | None = None):
    """Fetch an immutable GitHub PR snapshot, investigate it, and keep evidence locally."""
    from .github import fetch_pr
    snapshot = fetch_pr(url, destination)
    investigate(Path(snapshot["repo_path"]), base=snapshot["base_ref"], head=snapshot["head_ref"],
                app_entry=app_entry, description=snapshot["pr_description"], provider=provider,
                model=model, output=output, runner=runner, trust_local_code=trust_local_code, plan=plan)


@app.command()
def demo(output: Path = DEFAULT_OUTPUT, repo: Path | None = None, provider: str = "fixture",
         model: str | None = None, runner: str = "subprocess", repeats: int = 2):
    """Execute the authored cache regression demo. Fixture planner needs no API key."""
    from .demo import INTENT, create_demo_repo, demo_plan
    from .engine import investigate as execute
    from .gitops import diff_context, resolve_revision

    def run(destination):
        repository = create_demo_repo(destination)
        if provider == "fixture":
            probe_plan = demo_plan()
            provenance = {"name": "fixture", "model": "deterministic-demo", "live_ai": False,
                          "input_tokens": 0, "output_tokens": 0}
        else:
            context = diff_context(repository, resolve_revision(repository, "base"),
                                   resolve_revision(repository, "head"))
            probe_plan, provenance = plan_for(context, INTENT, None, provider, model, True)
        report = execute(repository, "base", "head", probe_plan, provider=provenance, output=output,
                         runner=runner, trusted_local=True, repeats=repeats)
        emit(report)
        typer.echo(f"Evidence: {output / report['id']}")

    if repo:
        run(repo)
    else:
        # Keep the exact Git objects available for replay after the command returns.
        run(output.resolve().parent / "fixtures" / ("tenant-cache-" + uuid.uuid4().hex[:12]))


@app.command()
def replay(report: str, repo: Annotated[Path, typer.Option("--repo")], case: str | None = None, output: Path = DEFAULT_OUTPUT,
           runner: str = "docker", trust_local_code: bool = False):
    """Rerun saved probes on the exact recorded SHAs, without another model call."""
    from .engine import investigate as execute
    path = Path(report)
    if path.is_file():
        original = json.loads(path.read_text())
    else:
        from .api import RUN_ID
        if not RUN_ID.fullmatch(report):
            raise ValueError("provide an evidence report.json path or a valid run ID")
        original = json.loads((output / report / "report.json").read_text())
    findings = [x for x in original["findings"] if case is None or x["id"] == case]
    if not findings:
        raise ValueError("no matching case in report")
    from .models import Probe
    fields = set(Probe.model_fields)
    plan = Plan.model_validate({"intent_summary": original["intent_summary"],
                                "probes": [{k: v for k, v in f.items() if k in fields} for f in findings]})
    result = execute(repo, original["repository"]["base_sha"], original["repository"]["head_sha"], plan,
                     provider={"name": "replay", "model": "saved-evidence", "live_ai": False,
                               "input_tokens": 0, "output_tokens": 0}, output=output,
                     app_entry=original.get("app_entry", "service:app"), runner=runner,
                     trusted_local=trust_local_code, repeats=original.get("repeats", 2),
                     timeout=original.get("timeout", 20), minimize_cases=False)
    emit(result)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8765, data: Path = DEFAULT_OUTPUT):
    """Serve the evidence API. Keep loopback binding unless behind your own access controls."""
    import uvicorn
    from .api import create_app
    uvicorn.run(create_app(data), host=host, port=port)


@app.command()
def evaluate(output: Path = Path(".branchlab/evaluation"), runner: str = "subprocess"):
    """Run named authored fixture checks; this is not a model-accuracy benchmark."""
    from .evaluate import run_evaluation
    scorecard = run_evaluation(output, runner=runner)
    typer.echo(json.dumps({k: scorecard[k] for k in (
        "id", "fixture_scenarios", "checks_total", "checks_passed", "all_passed", "duration_ms", "scorecard_path"
    )}, indent=2))
    if not scorecard["all_passed"]:
        raise typer.Exit(code=1)


@app.command()
def version():
    typer.echo(__version__)


if __name__ == "__main__":
    app()
