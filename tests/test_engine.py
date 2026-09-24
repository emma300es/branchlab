import pytest

from branchlab.engine import classify


def runs(*outcomes):
    return [{"outcome": x} for x in outcomes]


@pytest.mark.parametrize("base,head,intent,cited,expected", [
    (["passed", "passed"], ["failed", "failed"], "preserve", True, "suspected_regression"),
    (["passed", "passed"], ["failed", "failed"], "change", True, "intentional_change"),
    (["passed", "passed"], ["failed", "failed"], "unknown", True, "intent_unresolved"),
    (["passed", "passed"], ["failed", "failed"], "preserve", False, "intent_unresolved"),
    (["passed", "passed"], ["passed", "passed"], "preserve", True, "no_regression"),
    (["passed", "passed"], ["passed", "failed"], "preserve", True, "flaky"),
    (["failed", "passed"], ["failed", "failed"], "preserve", True, "flaky"),
    (["passed", "passed"], ["error", "error"], "preserve", True, "inconclusive"),
    (["passed", "passed"], ["timeout", "timeout"], "preserve", True, "inconclusive"),
    (["failed", "failed"], ["failed", "failed"], "preserve", True, "invalid_baseline"),
    (["failed", "failed"], ["passed", "passed"], "preserve", True, "invalid_baseline"),
])
def test_evidence_classification(base, head, intent, cited, expected):
    assert classify(runs(*base), runs(*head), intent, cited) == expected


def checked_run(outcome, failed_paths=()):
    return {"outcome": outcome, "checks": [
        {"step_id": "read-beta", "target": "json", "path": field, "operator": "eq",
         "expected": expected, "observed": None, "passed": field not in failed_paths}
        for field, expected in (("tenant", "beta"), ("content", "beta-private-note"))
    ]}


def test_same_outcome_different_failed_assertion_is_flaky():
    assert classify([checked_run("passed"), checked_run("passed")],
                    [checked_run("failed", ("tenant",)), checked_run("failed", ("content",))],
                    "preserve", True) == "flaky"


def test_minimization_rejects_different_failure_and_unstable_baseline(monkeypatch, tmp_path):
    from branchlab.demo import demo_plan
    from branchlab.engine import minimize
    original = demo_plan().probes[0]
    original_runs = ([checked_run("passed")] * 2, [checked_run("failed", ("tenant",))] * 2)
    paired = [
        ([checked_run("passed")] * 2, [checked_run("failed", ("content",))] * 2),
        ([checked_run("passed"), checked_run("failed", ("tenant",))],
         [checked_run("failed", ("tenant",))] * 2),
    ]
    monkeypatch.setattr("branchlab.engine._paired", lambda *args: paired.pop(0))
    minimal, accepted, evidence = minimize(original, tmp_path, tmp_path, "service:app", "subprocess", 20,
                                          True, 2, original_runs=original_runs)
    assert len(minimal.steps) == 3
    assert accepted is None
    assert evidence["attempts"] == 2


def test_minimization_accepts_only_same_stable_signature(monkeypatch, tmp_path):
    from branchlab.demo import demo_plan
    from branchlab.engine import minimize
    original = demo_plan().probes[0]
    original_runs = ([checked_run("passed")] * 2, [checked_run("failed", ("tenant",))] * 2)
    candidates = [original_runs, ([checked_run("passed")] * 2, [checked_run("passed")] * 2)]
    monkeypatch.setattr("branchlab.engine._paired", lambda *args: candidates.pop(0))
    minimal, accepted, evidence = minimize(original, tmp_path, tmp_path, "service:app", "subprocess", 20,
                                          True, 2, original_runs=original_runs)
    assert [step.id for step in minimal.steps] == ["warm-alpha", "read-beta"]
    assert accepted == original_runs
    assert evidence["status"] == "minimized"
