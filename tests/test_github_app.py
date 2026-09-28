import base64
import copy
from dataclasses import replace
import hashlib
import hmac
import json
import sqlite3
from types import SimpleNamespace

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from branchlab import github_app as appmod
from branchlab.demo import demo_plan

BASE, HEAD = "a" * 40, "b" * 40


@pytest.fixture
def config(tmp_path):
    return appmod.AppConfig(123, tmp_path / "app.pem", "webhook-test-secret-" * 3,
                           (appmod.RepositoryPolicy(456, 789, "example/service"),),
                           state_dir=tmp_path / "state", report_dir=tmp_path / "reports")


@pytest.fixture
def queue(tmp_path):
    return appmod.JobQueue(tmp_path / "queue.sqlite3")


def pr():
    return {"number": 12, "state": "open", "draft": False, "title": "Cache improvement",
            "body": "Preserve tenant isolation.",
            "base": {"sha": BASE, "repo": {"id": 789, "full_name": "example/service"}},
            "head": {"sha": HEAD, "repo": {"id": 987, "full_name": "fork/service"}}}


def payload():
    return {"action": "opened", "number": 12, "installation": {"id": 456},
            "repository": {"id": 789, "full_name": "example/service"}, "pull_request": pr()}


def snapshot(config):
    return appmod._snapshot(pr(), config.repositories[0])


def client(config, queue):
    app = FastAPI()
    app.include_router(appmod.create_webhook_router(config, queue))
    return TestClient(app)


def send(client, config, value=None, *, body=None, delivery="delivery-1", event="pull_request", signature=None):
    raw = body if body is not None else json.dumps(payload() if value is None else value).encode()
    signature = signature or "sha256=" + hmac.new(config.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/api/github/webhook", content=raw,
                       headers={"x-hub-signature-256": signature, "x-github-delivery": delivery,
                                "x-github-event": event, "content-type": "application/json"})


def test_signed_delivery_persisted_before_ack_and_replayed_once(config, queue):
    c = client(config, queue)
    one = send(c, config)
    assert one.status_code == 202 and one.json() == {"job_id": 1, "status": "queued", "duplicate": False}
    reopened = appmod.JobQueue(queue.path)
    duplicate = send(client(config, reopened), config)
    assert duplicate.json()["duplicate"] is True
    another_delivery = send(c, config, delivery="delivery-2")
    assert another_delivery.json()["duplicate"] is True
    assert len(reopened.list_jobs()) == 1
    assert reopened.get(1)["head_sha"] == HEAD
    assert "Preserve tenant" not in queue.path.read_bytes().decode(errors="ignore")


def test_delivery_id_cannot_be_reused_with_changed_content(config, queue):
    c = client(config, queue)
    send(c, config)
    forged = payload()
    forged["pull_request"]["head"]["sha"] = "c" * 40
    response = send(c, config, forged)
    assert response.status_code == 400
    assert len(queue.list_jobs()) == 1


@pytest.mark.parametrize("signature", ["sha256=bad", "sha1=" + "a" * 40, "sha256=" + "0" * 64])
def test_bad_signatures_never_queue(config, queue, signature):
    assert send(client(config, queue), config, signature=signature).status_code == 401
    assert queue.list_jobs() == []


def test_payload_limit_and_malformed_json(config, queue):
    c = client(config, queue)
    assert send(c, config, body=b"x" * (appmod.MAX_WEBHOOK_BYTES + 1)).status_code == 413
    assert send(c, config, body=b"{").status_code == 400
    assert send(c, config, body=b"[]").status_code == 400
    assert send(c, config, delivery="../bad").status_code == 400
    assert not queue.list_jobs()


@pytest.mark.parametrize("mutation", [
    lambda p: p["installation"].update(id=99),
    lambda p: p["installation"].update(id=True),
    lambda p: p["repository"].update(id=1),
    lambda p: p["repository"].update(full_name="attacker/service"),
    lambda p: p["pull_request"]["base"]["repo"].update(id=99),
    lambda p: p["pull_request"]["base"]["repo"].update(full_name="attacker/service"),
    lambda p: p["pull_request"]["head"].update(sha="--upload-pack=evil"),
    lambda p: p["pull_request"]["base"].update(sha="main"),
    lambda p: p.update(number=99),
    lambda p: p["pull_request"].update(number=True),
    lambda p: p["pull_request"].update(state="closed"),
    lambda p: p["pull_request"].update(body={"command": "evil"}),
])
def test_forged_repo_installation_or_refs_never_queue(config, queue, mutation):
    value = payload()
    mutation(value)
    assert send(client(config, queue), config, value).status_code == 400
    assert not queue.list_jobs()


@pytest.mark.parametrize("action", sorted(appmod.ACTIONS))
def test_supported_pr_actions(config, queue, action):
    value = payload()
    value["action"] = action
    assert send(client(config, queue), config, value).json()["status"] == "queued"


def test_unsupported_and_draft_events_are_ignored(config, queue):
    c = client(config, queue)
    assert send(c, config, event="push").json()["status"] == "ignored"
    assert send(c, config, event="ping").json()["status"] == "pong"
    value = payload()
    value["action"] = "closed"
    assert send(c, config, value).json()["status"] == "ignored"
    value["action"] = "opened"
    value["pull_request"]["draft"] = True
    assert send(c, config, value).json()["status"] == "ignored"
    assert not queue.list_jobs()


def test_queue_saturation_fails_retriably_without_ack(config, queue, monkeypatch):
    monkeypatch.setattr(appmod, "MAX_PENDING", 1)
    c = client(config, queue)
    send(c, config)
    value = payload()
    value["pull_request"]["head"]["sha"] = "c" * 40
    assert send(c, config, value, delivery="two").status_code == 503
    assert len(queue.list_jobs()) == 1


def test_lease_survives_restart_and_fences_old_worker(config, queue):
    queue.enqueue("one", "digest", snapshot(config))
    first = queue.claim()
    assert first and queue.claim() is None
    restarted = appmod.JobQueue(queue.path)
    assert restarted.claim() is None
    with sqlite3.connect(queue.path) as db:
        db.execute("UPDATE jobs SET lease_until=0")
    second = restarted.claim()
    assert second["attempts"] == 2 and first["lease"] != second["lease"]
    assert not queue.heartbeat(first)
    with pytest.raises(appmod.AppError, match="lease"):
        queue.finish(first, "completed")
    restarted.checkpoint(second, check_run_id=999, report_id="run-example")
    restarted.finish(second, "completed")
    assert queue.get(1)["report_id"] == "run-example"
    assert queue.get(1)["status"] == "completed"


def test_crash_retry_budget_and_explicit_resubmission(config, queue):
    queue.enqueue("one", "digest", snapshot(config))
    for _ in range(appmod.MAX_ATTEMPTS):
        assert queue.claim()
        with sqlite3.connect(queue.path) as db:
            db.execute("UPDATE jobs SET lease_until=0")
    assert queue.claim() is None
    assert queue.get(1)["status"] == "failed"
    assert queue.enqueue("redelivery", "digest", snapshot(config))["status"] == "failed"
    assert queue.enqueue("manual", "digest", snapshot(config), retry_failed=True)["status"] == "queued"


class FakeGitHub:
    def __init__(self, *, after=None, check_runs=None):
        self.calls = []
        self.reads = 0
        self.after = after
        self.check_runs = check_runs or []

    def installation_token(self, policy):
        self.calls.append(("token", policy.repository_id))
        return "installation-secret"

    def read_pr(self, policy, number, token):
        self.reads += 1
        self.calls.append(("read", number))
        return copy.deepcopy(self.after if self.reads > 1 and self.after else pr())

    def request(self, method, path, token, payload=None):
        self.calls.append((method, path, token, payload))
        if method == "GET":
            return {"check_runs": self.check_runs}
        return {"id": 500}


def test_owner_submission_uses_installation_and_allowlist(config, queue):
    fake = FakeGitHub()
    answer = appmod.enqueue_pr_url("https://github.com/example/service/pull/12", config, queue, client=fake)
    assert answer["status"] == "queued"
    assert fake.calls == [("token", 789), ("read", 12)]
    for url in ("https://github.com/attacker/service/pull/12", "https://example.com/example/service/pull/12"):
        with pytest.raises(appmod.AppError):
            appmod.enqueue_pr_url(url, config, queue, client=fake)
    assert len(fake.calls) == 2


def test_jwt_is_signed_rsa_and_token_is_repository_scoped(config):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    config.private_key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                        serialization.PrivateFormat.PKCS8,
                                                        serialization.NoEncryption()))
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(201, json={"token": "installation-secret"})

    gh = appmod.GitHubAppClient(config, transport=httpx.MockTransport(handler))
    assert gh.installation_token(config.repositories[0]) == "installation-secret"
    request = requests[0]
    assert str(request.url) == "https://api.github.com/app/installations/456/access_tokens"
    token = request.headers["authorization"].removeprefix("Bearer ")
    header, body, signature = token.split(".")
    decoded = json.loads(base64.urlsafe_b64decode(body + "=="))
    assert decoded["iss"] == "123" and decoded["exp"] - decoded["iat"] <= 600
    key.public_key().verify(base64.urlsafe_b64decode(signature + "=="), (header + "." + body).encode(),
                            padding.PKCS1v15(), hashes.SHA256())
    assert json.loads(request.content) == {"repository_ids": [789],
                                          "permissions": {"contents": "read", "pull_requests": "read", "checks": "write"}}
    assert "installation-secret" not in repr(config)
    assert config.webhook_secret not in repr(config)


@pytest.mark.parametrize("status", [302, 401, 403, 500])
def test_github_errors_do_not_follow_redirects_or_leak_secrets(config, status):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, headers={"location": "https://evil.example/steal"},
                              text="server echoed installation-secret")

    gh = appmod.GitHubAppClient(config, transport=httpx.MockTransport(handler))
    with pytest.raises(appmod.AppError) as error:
        gh.request("GET", "/repos/example/service", "installation-secret")
    assert "installation-secret" not in str(error.value)
    assert len(seen) == 1


def test_app_snapshot_never_uses_gh_fork_url_or_credentials_in_argv(config, tmp_path, monkeypatch):
    calls = []

    def git(args, env, timeout=120):
        calls.append((args, dict(env)))
        if "rev-parse" in args:
            return args[-1].removesuffix("^{commit}")
        return ""

    monkeypatch.setattr(appmod, "_git", git)
    monkeypatch.setenv("OPENAI_API_KEY", "host-only-secret")
    result = appmod.fetch_app_snapshot(FakeGitHub(), config.repositories[0], snapshot(config),
                                      tmp_path / "snapshot", "installation-secret")
    assert result["description"] == "Cache improvement\n\nPreserve tenant isolation."
    fetch = next(args for args, _ in calls if "fetch" in args)
    assert fetch[-3:] == ["https://github.com/example/service.git", BASE, HEAD]
    for args, env in calls:
        assert "installation-secret" not in " ".join(args)
        assert "gh" not in args and "checkout" not in args and "clone" not in args
        assert "http.followRedirects=false" in args and "credential.helper=" in args
        assert env["BRANCHLAB_INSTALLATION_TOKEN"] == "installation-secret"
        assert "OPENAI_API_KEY" not in env and "GH_TOKEN" not in env
        assert "fork/service" not in " ".join(args)


@pytest.mark.parametrize("field", ["base", "head", "body"])
def test_snapshot_races_rejected(config, tmp_path, monkeypatch, field):
    after = pr()
    if field == "body":
        after[field] = "New intent"
    else:
        after[field]["sha"] = "c" * 40
    monkeypatch.setattr(appmod, "_git", lambda args, env, **kw:
                        args[-1].removesuffix("^{commit}") if "rev-parse" in args else "")
    with pytest.raises(appmod.SnapshotChanged):
        appmod.fetch_app_snapshot(FakeGitHub(after=after), config.repositories[0], snapshot(config),
                                 tmp_path / "snapshot", "installation-secret")


def test_git_errors_omit_credential_stdout_and_stderr(monkeypatch):
    monkeypatch.setattr(appmod.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=128, stdout=b"installation-secret", stderr=b"installation-secret"))
    with pytest.raises(appmod.AppError) as error:
        appmod._git(["git", "fetch"], {})
    assert "installation-secret" not in str(error.value)


def fake_report():
    return {"id": "run-20260928-123456-1234abcd", "summary": {"suspected_regression": 1},
            "repository": {"name": "service", "base_sha": BASE, "head_sha": HEAD},
            "provider": {"name": "openai", "live_ai": True}, "intent_summary": "Test report", "runner": "docker",
            "limitations": [], "reproduce_command": "test-replay-command",
            "findings": [{"id": "tenant-check", "classification": "suspected_regression",
                          "title": "DO NOT POST THIS TITLE @everyone secret",
                          "rationale": "installation-secret", "invariant": "tenant isolation",
                          "head_runs": [{"outcome": "failed", "stdout": "private-code"}],
                          "base_runs": [{"outcome": "passed"}], "steps": [], "assertions": [],
                          "citations": [{"revision": "base", "path": "service.py", "line_start": 4,
                                         "line_end": 6, "quote": "private-code"}]}]}


def persist_fake_report(config):
    from branchlab.engine import save_report
    report = fake_report()
    report["provider"]["name"] = config.planner_provider
    save_report(report, demo_plan(), config.report_dir)
    return report


def test_check_run_uses_safe_summary_and_not_raw_evidence():
    report = fake_report()
    report["findings"][0]["citations"][0]["path"] = "@everyone [click](https://evil) <img>.py"
    result = appmod.check_output(report)
    assert result["conclusion"] == "failure"
    rendered = json.dumps(result)
    for prohibited in ("@everyone", "[click](", "<img>", "installation-secret", "private-code", "DO NOT POST"):
        assert prohibited not in rendered
    assert "&#64;everyone" in rendered and "&lt;img&gt;" in rendered


def test_worker_forces_docker_tool_free_planner_and_no_host_trust(config, queue, monkeypatch, tmp_path):
    from branchlab import engine, gitops, providers
    queue.enqueue("one", "digest", snapshot(config))
    fake = FakeGitHub()
    monkeypatch.setattr(appmod, "fetch_app_snapshot", lambda *a, **k: {"repo": tmp_path, "description": "test"})
    monkeypatch.setattr(gitops, "diff_context", lambda *a: {"files": {}})
    monkeypatch.setattr(providers, "build_plan", lambda *a, **k: (demo_plan(), {"name": "openai", "live_ai": True}))
    monkeypatch.setattr(providers, "build_plan_codex", lambda *a, **k: pytest.fail("agent planner forbidden"))
    seen = []

    def investigate(repo, base, head, plan, **kwargs):
        seen.append(kwargs)
        assert base == BASE and head == HEAD and len(plan.probes) <= 4
        return persist_fake_report(config)

    monkeypatch.setattr(engine, "investigate", investigate)
    result = appmod.AppWorker(config, queue, client=fake).run_once()
    assert result["status"] == "completed" and result["report_id"] == fake_report()["id"]
    assert seen[0]["runner"] == "docker" and seen[0]["trusted_local"] is False
    assert seen[0]["timeout"] == 10 and seen[0]["repeats"] == 2
    assert any(call[0] == "POST" and call[1].endswith("check-runs") for call in fake.calls)
    assert any(call[0] == "PATCH" and call[-1].get("conclusion") == "failure" for call in fake.calls)
    assert not any("comments" in str(call) for call in fake.calls)


def test_openclaw_planner_is_operator_selected_not_pr_selected(config, queue, monkeypatch, tmp_path):
    from branchlab import engine, gitops, providers
    config = replace(config, planner_provider="openclaw")
    queue.enqueue("one", "digest", snapshot(config))
    monkeypatch.setattr(appmod, "fetch_app_snapshot", lambda *a, **k: {"repo": tmp_path, "description": "test"})
    monkeypatch.setattr(gitops, "diff_context", lambda *a: {})
    monkeypatch.setattr(providers, "build_plan", lambda *a, **k: pytest.fail("wrong planner"))
    monkeypatch.setattr(providers, "build_plan_openclaw", lambda *a, **k:
                        (demo_plan(), {"name": "openclaw", "live_ai": True}))
    monkeypatch.setattr(engine, "investigate", lambda *a, **k: persist_fake_report(config))
    assert appmod.AppWorker(config, queue, client=FakeGitHub()).run_once()["status"] == "completed"


def test_worker_rejects_fixture_or_codex_as_live_app_plan(config, queue, monkeypatch, tmp_path):
    from branchlab import engine, gitops, providers
    queue.enqueue("one", "digest", snapshot(config))
    monkeypatch.setattr(appmod, "fetch_app_snapshot", lambda *a, **k: {"repo": tmp_path, "description": "test"})
    monkeypatch.setattr(gitops, "diff_context", lambda *a: {})
    monkeypatch.setattr(providers, "build_plan", lambda *a, **k: (demo_plan(), {"name": "codex", "live_ai": True}))
    monkeypatch.setattr(engine, "investigate", lambda *a, **k: pytest.fail("must not execute"))
    result = appmod.AppWorker(config, queue, client=FakeGitHub()).run_once()
    assert result["status"] == "failed" and "tool-free" in result["error"]


def test_worker_safely_reports_failure_and_recovers_existing_check(config, queue, monkeypatch, tmp_path):
    from branchlab import gitops, providers
    queue.enqueue("one", "digest", snapshot(config))
    fake = FakeGitHub(check_runs=[{"id": 700, "external_id": "branchlab-1", "app": {"id": config.app_id}}])
    monkeypatch.setattr(appmod, "fetch_app_snapshot", lambda *a, **k: {"repo": tmp_path, "description": "test"})
    monkeypatch.setattr(gitops, "diff_context", lambda *a: {})

    def fail(*a, **kw):
        raise ValueError("provider returned host-secret and private-source")

    monkeypatch.setattr(providers, "build_plan", fail)
    result = appmod.AppWorker(config, queue, client=fake).run_once()
    assert result["status"] == "failed"
    assert "host-secret" not in json.dumps(result) and "private-source" not in json.dumps(fake.calls)
    assert not any(call[0] == "POST" for call in fake.calls)
    assert any(call[0] == "PATCH" and call[1].endswith("/700") for call in fake.calls)


def test_worker_stale_pr_does_not_run_model(config, queue, monkeypatch):
    queue.enqueue("one", "digest", snapshot(config))

    def stale(*a, **k):
        raise appmod.SnapshotChanged("PR changed after enqueue; stale job skipped.")

    monkeypatch.setattr(appmod, "fetch_app_snapshot", stale)
    fake = FakeGitHub()
    result = appmod.AppWorker(config, queue, client=fake).run_once()
    assert result["status"] == "skipped" and len(fake.calls) == 1


def test_configuration_rejects_unsafe_provider_and_ambiguous_allowlist(config):
    with pytest.raises(appmod.AppError):
        replace(config, planner_provider="codex")
    with pytest.raises(appmod.AppError):
        replace(config, repositories=config.repositories * 2)
    with pytest.raises(appmod.AppError):
        replace(config, webhook_secret="short")
    with pytest.raises(appmod.AppError):
        appmod.RepositoryPolicy(True, 2, "owner/repo")
    with pytest.raises(appmod.AppError):
        appmod.RepositoryPolicy(1, 2, "owner/repo", app_entry="$(evil):app")


@pytest.mark.parametrize("category", ["intent_unresolved", "flaky", "inconclusive", "invalid_baseline"])
def test_incomplete_checks_cannot_pass_required_check(category):
    report = fake_report()
    report["summary"] = {category: 1}
    assert appmod.check_output(report)["conclusion"] == "action_required"


@pytest.mark.parametrize("summary,findings", [({}, []), ({"no_regression": 0}, [1]), ({"no_regression": 1}, [])])
def test_empty_measurement_is_not_a_success(summary, findings):
    report = {"summary": summary, "findings": findings}
    # These are malformed/incomplete reports. A non-dict finding isn't traversed.
    if findings == [1]:
        report["findings"] = [{"id": "empty", "citations": []}]
    assert appmod.check_output(report)["conclusion"] == "action_required"


@pytest.mark.parametrize("existing", [False, True])
def test_lost_lease_blocks_new_and_existing_check_mutations(config, queue, existing, monkeypatch):
    queue.enqueue("one", "digest", snapshot(config))
    job = queue.claim()
    if existing:
        job["check_run_id"] = 777
    fake = FakeGitHub()
    worker = appmod.AppWorker(config, queue, client=fake)
    monkeypatch.setattr(queue, "heartbeat", lambda job: False)
    with pytest.raises(appmod.AppError, match="lease"):
        worker._check(config.repositories[0], "installation-secret", job)
    assert not any(call[0] in {"POST", "PATCH"} for call in fake.calls)


def test_lost_lease_blocks_exception_check_update(config, queue, tmp_path, monkeypatch):
    from branchlab import gitops, providers
    queue.enqueue("one", "digest", snapshot(config))
    fake = FakeGitHub()
    monkeypatch.setattr(appmod, "fetch_app_snapshot", lambda *a, **k: {"repo": tmp_path, "description": "test"})
    monkeypatch.setattr(gitops, "diff_context", lambda *a: {})

    def fail(*a, **kw):
        monkeypatch.setattr(queue, "heartbeat", lambda job: False)
        raise ValueError("planner failed after lease loss")

    monkeypatch.setattr(providers, "build_plan", fail)
    appmod.AppWorker(config, queue, client=fake).run_once()
    assert len([call for call in fake.calls if call[0] == "POST"]) == 1
    assert not any(call[0] == "PATCH" for call in fake.calls)


def test_probe_budget_omissions_are_in_persisted_report(config, queue, monkeypatch, tmp_path):
    from branchlab import engine, gitops, providers
    from branchlab.models import Plan
    queue.enqueue("one", "digest", snapshot(config))
    plan = demo_plan()
    probes = [plan.probes[0].model_copy(update={"id": f"probe-{i}"}) for i in range(6)]
    oversized = probes[-1].model_dump()
    # All step IDs remain unique and existing assertions still refer to the original step.
    original = oversized["steps"][0]
    oversized["steps"] += [{**original, "id": f"extra-{i}"} for i in range(6)]
    probes[-1] = type(probes[-1]).model_validate(oversized)
    plan = Plan(intent_summary="Budget test", probes=probes)
    monkeypatch.setattr(appmod, "fetch_app_snapshot", lambda *a, **k: {"repo": tmp_path, "description": "test"})
    monkeypatch.setattr(gitops, "diff_context", lambda *a: {})
    monkeypatch.setattr(providers, "build_plan", lambda *a, **k: (plan, {"name": "openai", "live_ai": True}))
    monkeypatch.setattr(engine, "investigate", lambda *a, **k: persist_fake_report(config))
    result = appmod.AppWorker(config, queue, client=FakeGitHub()).run_once()
    assert result["status"] == "completed"
    report = json.loads((config.report_dir / result["report_id"] / "report.json").read_text())
    assert report["probe_budget"] == {"proposed": 6, "selected": 4, "omitted": 2, "over_step_limit": 1,
                                      "over_probe_limit": 1, "limits": {"probes": 4, "steps_per_probe": 5}}
    assert any("omitted probes were not executed" in line for line in report["limitations"])
    assert "2 omitted" in appmod.check_output(report)["output"]["summary"]


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(runner="subprocess"),
    lambda r: r["repository"].update(head_sha="c" * 40),
    lambda r: r["repository"].update(base_sha="c" * 40),
    lambda r: r["provider"].update(name="codex"),
    lambda r: r["provider"].update(live_ai=False),
    lambda r: r.update(id="../report"),
])
def test_cached_report_must_match_revisions_identity_and_isolation(config, queue, mutation):
    report = fake_report()
    mutation(report)
    with pytest.raises(appmod.AppError, match="identity, revisions or isolation"):
        appmod.AppWorker(config, queue)._validate_report(report, snapshot(config), fake_report()["id"])
