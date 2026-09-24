import json
from pathlib import Path

import pytest

from branchlab.evaluate import _score, run_evaluation


def test_fixture_evaluation_uses_real_paired_engine_reports(tmp_path):
    scorecard = run_evaluation(tmp_path)
    assert scorecard["evaluation_type"] == "authored_fixture_integration"
    assert scorecard["live_ai"] is False
    assert scorecard["fixture_scenarios"] == 5
    assert scorecard["checks_total"] == scorecard["checks_passed"] == 9
    assert scorecard["all_passed"] is True
    assert json.loads(Path(scorecard["scorecard_path"]).read_text()) == scorecard
    expected = {
        "mixed-change": {"suspected_regression", "intentional_change", "no_regression"},
        "intentional-only": {"intentional_change", "no_regression"},
        "unspecified-intent": {"intent_unresolved"},
        "invalid-baseline": {"invalid_baseline"},
        "head-import-error": {"inconclusive"},
    }
    for scenario in scorecard["scenarios"]:
        assert scenario["passed"] is True
        assert (Path(scenario["repository_path"]) / ".git").is_dir()
        report = json.loads(Path(scenario["report_path"]).read_text())
        assert report["provider"]["live_ai"] is False
        assert report["repeats"] == 2
        assert {finding["classification"] for finding in report["findings"]} == expected[scenario["id"]]
        assert all(finding["citation_validation"]["valid"] for finding in report["findings"])
        assert report["repository"]["base_sha"] == scenario["base_sha"]
        assert report["repository"]["head_sha"] == scenario["head_sha"]
        for check in scenario["checks"]:
            if check["kind"] == "classification_and_outcomes":
                finding = next(f for f in report["findings"] if f["id"] == check["probe_id"])
                assert check["observed"]["base_outcomes"] == [run["outcome"] for run in finding["base_runs"]]
                assert check["observed"]["head_outcomes"] == [run["outcome"] for run in finding["head_runs"]]
    assert any("not a broad benchmark" in limit for limit in scorecard["limitations"])


def test_score_reports_actual_mismatch_instead_of_expected_success():
    report = {"findings": [{"id": "case", "classification": "inconclusive",
                            "base_runs": [{"outcome": "error"}], "head_runs": [{"outcome": "error"}]}]}
    expected = {"case": {"classification": "suspected_regression",
                         "base_outcomes": ["passed", "passed"], "head_outcomes": ["failed", "failed"]}}
    checks = _score(report, expected)
    assert checks[0]["passed"] is False
    assert checks[0]["observed"]["classification"] == "inconclusive"
    assert checks[0]["expected"]["classification"] == "suspected_regression"


def test_missing_finding_is_a_failed_check():
    checks = _score({"findings": []}, {"case": {"classification": "no_regression"}})
    assert checks[0]["passed"] is False
    assert checks[0]["observed"]["classification"] is None


def test_invalid_runner_is_rejected_before_writing(tmp_path):
    with pytest.raises(ValueError, match="runner"):
        run_evaluation(tmp_path, runner="arbitrary-command")
    assert not list(tmp_path.iterdir())
