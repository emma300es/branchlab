"""Paired execution, conservative classification and bounded counterexample minimization."""

import json
import shlex
import tempfile
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .gitops import diff_context, export_revision, resolve_revision
from .models import Plan, Probe
from .providers import validate_citations
from .runner import run_probe

CLASSES = ("suspected_regression", "intentional_change", "intent_unresolved", "no_regression",
           "flaky", "inconclusive", "invalid_baseline")


def _failed_checks(run: dict) -> tuple:
    """Identity of each failed assertion, separate from an HTTP/runtime outcome."""
    return tuple(sorted(
        (check.get("step_id", ""), check.get("target", ""), check.get("path", ""),
         check.get("operator", ""), json.dumps(check.get("expected"), sort_keys=True))
        for check in run.get("checks", []) if check.get("passed") is False
    ))


def _stable_signature(base_runs: list[dict], head_runs: list[dict]) -> tuple | None:
    if (not base_runs or not head_runs or
            any(r.get("outcome") != "passed" or not r.get("checks") or
                any(c.get("passed") is not True for c in r["checks"]) for r in base_runs) or
            any(r.get("outcome") != "failed" for r in head_runs)):
        return None
    signatures = {_failed_checks(run) for run in head_runs}
    return next(iter(signatures)) if len(signatures) == 1 and () not in signatures else None


def classify(base_runs: list[dict], head_runs: list[dict], intent: str, citation_valid: bool) -> str:
    base = {r["outcome"] for r in base_runs}
    head = {r["outcome"] for r in head_runs}
    if not base or not head:
        return "inconclusive"
    if len(base) > 1 or len(head) > 1:
        return "flaky"
    # Stable outcome words alone do not prove that the same invariant failed.
    for runs in (base_runs, head_runs):
        signatures = {_failed_checks(r) for r in runs if r["outcome"] == "failed" and r.get("checks")}
        if len(signatures) > 1:
            return "flaky"
    if base & {"error", "timeout"} or head & {"error", "timeout"}:
        return "inconclusive"
    if base != {"passed"}:
        return "invalid_baseline"
    if head == {"passed"}:
        return "no_regression"
    if head != {"failed"}:
        return "inconclusive"
    if not citation_valid or intent == "unknown":
        return "intent_unresolved"
    return "intentional_change" if intent == "change" else "suspected_regression"


def _paired(base_dir, head_dir, app_entry, probe, runner, timeout, trusted_local, repeats):
    kwargs = {"runner": runner, "timeout": timeout, "trusted_local": trusted_local}
    return ([run_probe(base_dir, app_entry, probe, **kwargs) for _ in range(repeats)],
            [run_probe(head_dir, app_entry, probe, **kwargs) for _ in range(repeats)])


def minimize(probe, base_dir, head_dir, app_entry, runner, timeout, trusted_local, repeats, max_attempts=12,
             *, original_runs=None):
    """Greedy deletion, preserving asserted step IDs and a stable paired failure signature."""
    original = len(probe.steps)
    current = probe
    required = {a.step_id for a in probe.assertions}
    attempts = 0
    accepted_runs = None
    if original_runs is None:
        original_runs = _paired(base_dir, head_dir, app_entry, probe, runner, timeout, trusted_local, repeats)
    signature = _stable_signature(*original_runs)
    if signature is None:
        return current, None, {"original_steps": original, "minimized_steps": original, "attempts": 0,
                               "status": "unstable_or_missing_signature"}
    for step in list(probe.steps):
        if step.id in required or attempts >= max_attempts:
            continue
        candidate = Probe.model_validate({**current.model_dump(),
                                          "steps": [s.model_dump() for s in current.steps if s.id != step.id]})
        attempts += 1
        br, hr = _paired(base_dir, head_dir, app_entry, candidate, runner, timeout, trusted_local, repeats)
        if _stable_signature(br, hr) == signature:
            current, accepted_runs = candidate, (br, hr)
    return current, accepted_runs, {"original_steps": original, "minimized_steps": len(current.steps),
                                    "attempts": attempts,
                                    "status": "minimized" if len(current.steps) < original else "unchanged"}


def investigate(repo: Path, base_ref: str, head_ref: str, plan: Plan, *, provider: dict,
                output: Path, app_entry="service:app", runner="docker", trusted_local=False,
                repeats=2, timeout=20, minimize_cases=True) -> dict:
    if repeats < 2 or repeats > 5:
        raise ValueError("repeats must be between 2 and 5")
    if not 1 <= timeout <= 120:
        raise ValueError("timeout must be between 1 and 120 seconds")
    if runner not in {"docker", "subprocess"}:
        raise ValueError("runner must be docker or subprocess")
    if runner == "subprocess" and not trusted_local:
        raise ValueError("subprocess requires explicit trust in repository code")
    started = time.monotonic()
    repo = repo.resolve()
    base_sha, head_sha = resolve_revision(repo, base_ref), resolve_revision(repo, head_ref)
    context = diff_context(repo, base_sha, head_sha)
    validations = {v["probe_id"]: v for v in validate_citations(plan, context)}
    run_id = "run-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    output = output.resolve()
    report_path = output / run_id / "report.json"
    reproduce_command = (f"branchlab replay {shlex.quote(str(report_path))} --repo {shlex.quote(str(repo))} "
                         f"--output {shlex.quote(str(output))}")
    if runner == "subprocess":
        reproduce_command += " --runner subprocess --trust-local-code"
    findings = []
    with tempfile.TemporaryDirectory(prefix="branchlab-execution-") as work:
        base_dir, head_dir = Path(work) / "base", Path(work) / "head"
        export_revision(repo, base_sha, base_dir)
        export_revision(repo, head_sha, head_dir)
        for probe in plan.probes:
            validation = validations.get(probe.id, {"valid": False, "errors": ["no validated citation"]})
            br, hr = _paired(base_dir, head_dir, app_entry, probe, runner, timeout, trusted_local, repeats)
            category = classify(br, hr, probe.intent, validation["valid"])
            minimal = probe
            original_evidence = None
            minimization = {"original_steps": len(probe.steps), "minimized_steps": len(probe.steps),
                            "attempts": 0, "status": "not_applicable"}
            if category == "suspected_regression" and minimize_cases:
                minimal, accepted, minimization = minimize(probe, base_dir, head_dir, app_entry, runner,
                                                          timeout, trusted_local, repeats, original_runs=(br, hr))
                if accepted:
                    original_evidence = {"steps": [step.model_dump() for step in probe.steps],
                                         "base_runs": br, "head_runs": hr}
                    br, hr = accepted
            findings.append({**minimal.model_dump(), "classification": category,
                             "citation_validation": {"valid": validation["valid"],
                                                     "errors": validation["errors"]},
                             "base_runs": br, "head_runs": hr, "minimization": minimization,
                             "original_evidence": original_evidence,
                             "reproducer": f"{reproduce_command} --case {shlex.quote(probe.id)}"})
    counts = Counter(f["classification"] for f in findings)
    limitations = ["Behavioral differences are evidence, not proof that a change is unintended.",
                   "Citation matching checks source grounding, not the correctness of the model's interpretation.",
                   "A finite probe set cannot establish the absence of regressions."]
    limitations.extend(context.get("limitations", []))
    if context.get("omitted_files"):
        limitations.append(f"Source context omitted {len(context['omitted_files'])} files under its content/size limits.")
    if runner == "subprocess":
        limitations.append("Trusted-local subprocess execution is not a security sandbox; use Docker for untrusted code.")
    if not provider.get("live_ai"):
        limitations.append("This run uses an explicit fixture/supplied plan, not a live AI-generated plan.")
    report = {"schema_version": "1.0", "id": run_id,
              "created_at": datetime.now(timezone.utc).isoformat(),
              "repository": {"name": repo.name, "base_ref": base_ref, "head_ref": head_ref,
                             "base_sha": base_sha, "head_sha": head_sha},
              "provider": provider, "runner": runner, "repeats": repeats, "timeout": timeout, "app_entry": app_entry,
              "duration_ms": round((time.monotonic() - started) * 1000),
              "intent_summary": plan.intent_summary, "changed_files": context["changed_files"],
              "impact_graph": context["impact_graph"], "summary": {c: counts[c] for c in CLASSES},
              "findings": findings, "limitations": limitations,
              "reproduce_command": reproduce_command}
    save_report(report, plan, output)
    return report


def markdown_report(report: dict) -> str:
    lines = [f"# BranchLab · {report['repository']['name']}", "", f"Run: `{report['id']}`", "",
             f"Base: `{report['repository']['base_sha']}`", f"Head: `{report['repository']['head_sha']}`", "",
             f"Planner: **{report['provider']['name']}** · live AI: **{report['provider']['live_ai']}**", "",
             report["intent_summary"], "", "## Findings", ""]
    for finding in report["findings"]:
        lines += [f"### {finding['title']}", "", f"Classification: **{finding['classification']}**", "",
                  finding["rationale"], "", f"Invariant: {finding['invariant']}", "",
                  f"Base outcomes: {', '.join(r['outcome'] for r in finding['base_runs'])}",
                  f"Head outcomes: {', '.join(r['outcome'] for r in finding['head_runs'])}", ""]
        for citation in finding["citations"]:
            lines.append(f"- `{citation['revision']}:{citation['path']}:{citation['line_start']}` — {citation['quote']}")
        lines += ["", "Probe (declarative, no generated executable code):", "", "```json",
                  json.dumps({"steps": finding["steps"], "assertions": finding["assertions"]}, indent=2), "```", ""]
    lines += ["## Reproduce", "", "```sh", report["reproduce_command"], "```", "",
              "Commands for trusted-local runs explicitly retain that choice. Use subprocess only for code you trust; Docker is the default otherwise.", "",
              "## Limits", "", *[f"- {x}" for x in report["limitations"]], ""]
    return "\n".join(lines)


def save_report(report: dict, plan: Plan, output: Path) -> Path:
    directory = output / report["id"]
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "plan.json").write_text(plan.model_dump_json(indent=2) + "\n")
    (directory / "report.md").write_text(markdown_report(report))
    temporary = directory / "report.tmp"
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(directory / "report.json")
    return directory
