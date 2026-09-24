import json

from fastapi.testclient import TestClient

from branchlab.api import create_app
from branchlab.demo import create_demo_repo, demo_plan
from branchlab.engine import investigate


def test_real_git_differential_and_minimization(tmp_path):
    repo = create_demo_repo(tmp_path / "tenant-cache")
    report = investigate(repo, "base", "head", demo_plan(), output=tmp_path / "runs",
                         provider={"name": "fixture", "model": "fixture", "live_ai": False},
                         runner="subprocess", trusted_local=True)
    assert report["summary"]["suspected_regression"] == 1
    assert report["summary"]["intentional_change"] == 1
    assert report["summary"]["no_regression"] == 1
    leak = next(f for f in report["findings"] if f["id"] == "tenant-isolation")
    assert leak["minimization"]["minimized_steps"] == 2
    assert all(r["outcome"] == "passed" for r in leak["base_runs"])
    assert all(r["outcome"] == "failed" for r in leak["head_runs"])
    assert leak["head_runs"][0]["responses"][-1]["body"]["tenant"] == "alpha"
    assert leak["base_runs"][0]["responses"][-1]["body"]["tenant"] == "beta"
    persisted = json.loads((tmp_path / "runs" / report["id"] / "report.json").read_text())
    assert persisted == report
    client = TestClient(create_app(tmp_path / "runs"), base_url="http://127.0.0.1")
    assert client.get("/api/runs").json()[0]["id"] == report["id"]
    assert client.get(f"/api/runs/{report['id']}").json() == report
    assert client.get(f"/api/runs/{report['id']}/report.md").status_code == 200


def test_api_rejects_path_traversal_and_nonlocal_origin(tmp_path):
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1")
    assert client.get("/api/runs/not-a-run").status_code == 404
    assert client.post("/api/demo", headers={"origin": "https://untrusted.example"}).status_code == 403
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/runs", headers={"host": "rebind.attacker.example"}).status_code == 400


def test_demo_commits_are_reproducible(tmp_path):
    from branchlab.gitops import resolve_revision
    first = create_demo_repo(tmp_path / "first")
    second = create_demo_repo(tmp_path / "second")
    for ref in ("base", "head"):
        assert resolve_revision(first, ref) == resolve_revision(second, ref)


def test_model_probe_validation():
    from branchlab.models import Plan
    import pytest
    p = demo_plan().model_dump()
    p["probes"][0]["steps"][0]["path"] = "https://external.example/"
    with pytest.raises(ValueError):
        Plan.model_validate(p)
