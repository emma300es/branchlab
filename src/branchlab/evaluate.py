"""Measured integration evaluation on bundled, explicitly authored fixtures only.

This does not evaluate a model, arbitrary repositories, or general bug-finding
accuracy. Each result is derived from real engine reports and isolated paired
HTTP executions, including conservative classification and minimization checks.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
import uuid

from .demo import BASE, create_demo_repo, demo_plan
from .engine import investigate
from .models import Plan

FIXTURE_PROVIDER = {
    "name": "fixture", "model": "authored-evaluation", "live_ai": False,
    "input_tokens": 0, "output_tokens": 0,
}


def _variant(repository: Path, source: str, *, change_description: str | None = None) -> None:
    """Commit only source selected by this module, never supplied external code."""
    (repository / "service.py").write_text(source, encoding="utf-8")
    if change_description is not None:
        (repository / "CHANGE.md").write_text(change_description, encoding="utf-8")
    git = [
        "git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
        "-c", "user.name=BranchLab Evaluation", "-c", "user.email=evaluation@branchlab.invalid",
        "-C", str(repository),
    ]
    for arguments in (["add", "service.py", "CHANGE.md"], ["commit", "-m", "Authored evaluation variant"],
                      ["tag", "-f", "head"]):
        subprocess.run(git + arguments, capture_output=True, text=True, check=True, timeout=15)


def _subset(*probe_ids: str) -> Plan:
    plan = demo_plan()
    return Plan.model_validate({
        "intent_summary": plan.intent_summary,
        "probes": [p.model_dump() for p in plan.probes if p.id in probe_ids],
    })


def _expect(category: str, base: str = "passed", head: str = "failed") -> dict:
    return {"classification": category, "base_outcomes": [base, base], "head_outcomes": [head, head]}


def _score(report: dict, expected: dict[str, dict], *, check_minimization: bool = False) -> list[dict]:
    findings = {finding["id"]: finding for finding in report["findings"]}
    checks = []
    for probe_id, expectation in expected.items():
        finding = findings.get(probe_id, {})
        observed = {
            "classification": finding.get("classification"),
            "base_outcomes": [run["outcome"] for run in finding.get("base_runs", [])],
            "head_outcomes": [run["outcome"] for run in finding.get("head_runs", [])],
        }
        checks.append({
            "probe_id": probe_id, "kind": "classification_and_outcomes", "expected": expectation,
            "observed": observed, "passed": observed == expectation,
        })
    if check_minimization:
        finding = findings.get("tenant-isolation", {})
        minimization = finding.get("minimization", {})
        expectation = {"status": "minimized", "original_steps": 3, "minimized_steps": 2,
                       "step_ids": ["warm-alpha", "read-beta"]}
        observed = {key: minimization.get(key) for key in ("status", "original_steps", "minimized_steps")}
        observed["step_ids"] = [step["id"] for step in finding.get("steps", [])]
        checks.append({"probe_id": "tenant-isolation", "kind": "minimal_reproducer",
                       "expected": expectation, "observed": observed, "passed": observed == expectation})
    return checks


def run_evaluation(output: Path, runner: str = "subprocess") -> dict:
    """Run five authored fixture scenarios and save an evidence-backed scorecard.

    There is deliberately no repository argument, model choice or network path.
    Subprocess trust is scoped to locally generated bundled fixture code; it does
    not authorize running arbitrary repositories. Source repositories, plans and
    full engine reports remain alongside the scorecard for reproducibility.
    """
    if runner not in {"subprocess", "docker"}:
        raise ValueError("evaluation runner must be subprocess or docker")
    started = time.monotonic()
    evaluation_id = "evaluation-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    directory = Path(output).resolve() / evaluation_id
    directory.mkdir(parents=True, exist_ok=False)
    fixture_root = directory / "fixtures"
    mixed_repo = create_demo_repo(fixture_root / "mixed-change")
    intentional_repo = create_demo_repo(fixture_root / "intentional-only")
    _variant(intentional_repo, BASE.replace("CACHE_TTL = 60", "CACHE_TTL = 120"))
    unknown_repo = create_demo_repo(fixture_root / "unspecified-intent")
    _variant(unknown_repo, BASE.replace("CACHE_TTL = 60", "CACHE_TTL = 120"),
             change_description="# Change request\nAdjust caching implementation.\n")
    broken_repo = create_demo_repo(fixture_root / "head-import-error")
    _variant(broken_repo, 'raise RuntimeError("authored import-failure fixture")\n' + BASE)

    unknown_plan = _subset("cache-expiration")
    probe = unknown_plan.probes[0]
    probe.intent = "unknown"
    probe.rationale = "The authored change request does not establish whether the TTL change is intended."
    probe.citations = [demo_plan().probes[2].citations[0].model_copy(update={
        "line_start": 4, "line_end": 4, "quote": "The existing cache TTL is 60 seconds.",
    })]
    unknown_plan.intent_summary = "The fixture leaves intent unresolved; do not claim an accidental regression."
    invalid_plan = _subset("health-stability")
    invalid_plan.probes[0].assertions[0].expected_json = '"not-a-real-baseline-value"'
    invalid_plan.probes[0].rationale = "Deliberately wrong authored baseline assertion tests conservative handling."

    scenarios = [
        ("mixed-change", "Regression, intentional change, and unchanged control", mixed_repo, demo_plan(), {
            "tenant-isolation": _expect("suspected_regression"),
            "cache-expiration": _expect("intentional_change"),
            "health-stability": _expect("no_regression", head="passed"),
        }, True),
        ("intentional-only", "Independent head preserves tenant isolation and changes only TTL",
         intentional_repo, _subset("tenant-isolation", "cache-expiration"), {
             "tenant-isolation": _expect("no_regression", head="passed"),
             "cache-expiration": _expect("intentional_change"),
         }, False),
        ("unspecified-intent", "Behavioral change with deliberately unresolved intent", unknown_repo, unknown_plan,
         {"cache-expiration": _expect("intent_unresolved")}, False),
        ("invalid-baseline", "An assertion already wrong on base is not a new regression", mixed_repo, invalid_plan,
         {"health-stability": _expect("invalid_baseline", base="failed", head="failed")}, False),
        ("head-import-error", "A runtime import error is not an assertion failure", broken_repo,
         _subset("health-stability"), {"health-stability": _expect("inconclusive", head="error")}, False),
    ]
    results = []
    for scenario_id, title, repository, plan, expected, check_minimization in scenarios:
        report = investigate(
            repository, "base", "head", plan, provider=dict(FIXTURE_PROVIDER), output=directory / "reports",
            runner=runner, trusted_local=(runner == "subprocess"), repeats=2, timeout=10,
            minimize_cases=check_minimization,
        )
        checks = _score(report, expected, check_minimization=check_minimization)
        results.append({
            "id": scenario_id, "title": title, "repository_path": str(repository),
            "report_id": report["id"], "report_path": str(directory / "reports" / report["id"] / "report.json"),
            "base_sha": report["repository"]["base_sha"], "head_sha": report["repository"]["head_sha"],
            "duration_ms": report["duration_ms"], "checks": checks,
            "passed": all(check["passed"] for check in checks),
        })
    checks = [check for scenario in results for check in scenario["checks"]]
    scorecard_path = directory / "scorecard.json"
    scorecard = {
        "schema_version": "1.0", "id": evaluation_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_type": "authored_fixture_integration", "live_ai": False, "runner": runner,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "fixture_scenarios": len(results), "checks_total": len(checks),
        "checks_passed": sum(check["passed"] for check in checks),
        "all_passed": all(check["passed"] for check in checks),
        "scenarios": results, "scorecard_path": str(scorecard_path),
        "limitations": [
            "Five small authored fixture scenarios with hand-written probes; not a broad benchmark or AI accuracy score.",
            "No AI provider was called, and model planning quality or prompt-injection resistance is not evaluated here.",
            "Checks derive from real paired HTTP runs, two repeats per revision, plus bounded minimization attempts.",
            "The intentional-only and unresolved-intent variants are constructed controls, not naturally occurring PRs.",
            "Finite fixture coverage cannot establish the absence of other classification or execution defects.",
            "Subprocess mode explicitly trusts only bundled authored code and is not a security sandbox."
            if runner == "subprocess" else "Docker results depend on the locally built runner image and runtime.",
        ],
    }
    temporary = directory / "scorecard.tmp"
    temporary.write_text(json.dumps(scorecard, indent=2) + "\n", encoding="utf-8")
    temporary.replace(scorecard_path)
    return scorecard
