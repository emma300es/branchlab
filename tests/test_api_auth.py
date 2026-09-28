import pytest
from fastapi.testclient import TestClient

from branchlab.api import create_app

TOKEN = "test-api-token-" + "x" * 32


def test_remote_mode_requires_https_and_strong_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("BRANCHLAB_PUBLIC_ORIGIN", "https://branchlab.example")
    monkeypatch.delenv("BRANCHLAB_API_TOKEN", raising=False)
    with pytest.raises(ValueError):
        create_app(tmp_path)
    monkeypatch.setenv("BRANCHLAB_API_TOKEN", TOKEN)
    for origin in ["http://branchlab.example", "https://user:password@branchlab.example", "https://branchlab.example/path"]:
        monkeypatch.setenv("BRANCHLAB_PUBLIC_ORIGIN", origin)
        with pytest.raises(ValueError):
            create_app(tmp_path)


def test_remote_api_protects_evidence_and_mutations(tmp_path, monkeypatch):
    monkeypatch.setenv("BRANCHLAB_PUBLIC_ORIGIN", "https://branchlab.example")
    monkeypatch.setenv("BRANCHLAB_API_TOKEN", TOKEN)
    client = TestClient(create_app(tmp_path), base_url="https://branchlab.example")
    assert client.get("/api/health").status_code == 200
    for path in ["/api/runs", "/api/jobs", "/api/jobs/1"]:
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"authorization": "Bearer incorrect"}).status_code == 401
    assert client.post("/api/demo").status_code == 401
    assert client.post("/api/investigations", json={"pr_url": "https://github.com/a/b/pull/1"}).status_code == 401
    authenticated = {"authorization": f"Bearer {TOKEN}"}
    assert client.get("/api/runs", headers=authenticated).json() == []
    assert client.get("/api/jobs", headers=authenticated).json() == []
    assert client.get("/api/jobs/1", headers=authenticated).status_code == 404
    assert client.post("/api/investigations", headers=authenticated, json={"pr_url": "https://github.com/a/b/pull/1"}).status_code == 503
    assert client.get("/api/runs", headers={**authenticated, "host": "attacker.example"}).status_code == 400
    assert client.get("/docs", headers=authenticated).status_code == 404
    assert client.get("/openapi.json", headers=authenticated).status_code == 404


def test_webhook_exemption_does_not_expose_other_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("BRANCHLAB_API_TOKEN", TOKEN)
    client = TestClient(create_app(tmp_path), base_url="http://localhost")
    assert client.post("/api/github/webhook").status_code == 404  # No app configured.
    assert client.get("/api/github/webhook/other").status_code == 401
    assert client.get("/api/runs").status_code == 401


def test_remote_serve_rejects_unauthenticated_binding(monkeypatch):
    from typer.testing import CliRunner
    from branchlab.cli import app
    monkeypatch.delenv("BRANCHLAB_PUBLIC_ORIGIN", raising=False)
    result = CliRunner().invoke(app, ["serve", "--host", "0.0.0.0"])
    assert result.exit_code != 0
    assert "Non-loopback" in result.output


def test_signed_webhook_queues_without_bearer_but_jobs_remain_private(tmp_path, monkeypatch):
    import hashlib
    import hmac
    import json

    secret = "disposable-webhook-test-secret-" * 2
    settings = {
        "BRANCHLAB_PUBLIC_ORIGIN": "https://branchlab.example",
        "BRANCHLAB_API_TOKEN": TOKEN,
        "BRANCHLAB_GITHUB_APP_ID": "123",
        "BRANCHLAB_GITHUB_APP_PRIVATE_KEY_FILE": str(tmp_path / "unused-for-receiving.pem"),
        "BRANCHLAB_GITHUB_WEBHOOK_SECRET": secret,
        "BRANCHLAB_GITHUB_ALLOWLIST_JSON": json.dumps([
            {"installation_id": 456, "repository_id": 789, "full_name": "example/service"}]),
        "BRANCHLAB_GITHUB_STATE_DIR": str(tmp_path / "app-state"),
        "BRANCHLAB_DATA_DIR": str(tmp_path / "reports"),
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    client = TestClient(create_app(), base_url="https://branchlab.example")
    payload = {"action": "opened", "number": 12, "installation": {"id": 456},
               "repository": {"id": 789, "full_name": "example/service"},
               "pull_request": {"number": 12, "state": "open", "draft": False, "title": "Change",
                                "body": "Keep contract", "base": {"sha": "a" * 40,
                                "repo": {"id": 789, "full_name": "example/service"}},
                                "head": {"sha": "b" * 40, "repo": {"id": 1234}}}}
    raw = json.dumps(payload).encode()
    assert client.post("/api/github/webhook", content=raw).status_code == 401
    headers = {"x-hub-signature-256": "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(),
               "x-github-event": "pull_request", "x-github-delivery": "app-integration-1"}
    accepted = client.post("/api/github/webhook", content=raw, headers=headers)
    assert accepted.status_code == 202 and accepted.json()["job_id"] == 1
    assert client.get("/api/jobs").status_code == 401
    # Reopen both HTTP app and SQLite: duplicate delivery does not create a job.
    restarted = TestClient(create_app(), base_url="https://branchlab.example")
    assert restarted.post("/api/github/webhook", content=raw, headers=headers).json()["duplicate"] is True
    auth = {"authorization": f"Bearer {TOKEN}"}
    jobs = restarted.get("/api/jobs", headers=auth).json()
    assert len(jobs) == 1 and jobs[0]["status"] == "queued"
    assert restarted.get("/api/jobs/1", headers=auth).json()["head_sha"] == "b" * 40
