import copy
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from branchlab import github

BASE = "a" * 40
HEAD = "b" * 40


def metadata():
    return {"number": 12, "title": "Cache improvement", "body": "Keep tenant isolation.",
            "base": {"sha": BASE, "repo": {"id": 1, "full_name": "example/service"}},
            "head": {"sha": HEAD, "repo": {"id": 2, "full_name": "contributor/service"}}}


def mock_remote(monkeypatch, *, after=None, object_sha=None):
    calls = []
    reads = 0
    def run(args, **kwargs):
        nonlocal reads
        calls.append((args, kwargs))
        if args[0] == "gh":
            reads += 1
            return json.dumps(metadata() if reads == 1 or after is None else after)
        if "rev-parse" in args:
            return (object_sha or args[-1].removesuffix("^{commit}")) + "\n"
        return ""
    monkeypatch.setattr(github, "_run", run)
    return calls


def test_read_only_ingestion_uses_immutable_git_objects(monkeypatch, tmp_path):
    calls = mock_remote(monkeypatch)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    result = github.fetch_pr("https://github.com/example/service/pull/12", tmp_path / "snapshot")
    assert result["base_ref"] == BASE and result["head_ref"] == HEAD
    assert result["pr_description"] == "Cache improvement\n\nKeep tenant isolation."
    assert sum(args[0] == "gh" for args, _ in calls) == 2
    clone = next(args for args, _ in calls if "clone" in args)
    assert "--no-checkout" in clone and "--no-recurse-submodules" in clone
    assert clone[-2] == "https://github.com/example/service.git"
    fetch = next(args for args, _ in calls if "fetch" in args)
    assert fetch[-2:] == [BASE, HEAD]
    assert not any(op in args for args, _ in calls for op in ("push", "checkout", "comment", "POST"))
    for args, kwargs in calls:
        if args[0] == "git":
            assert "GIT_CONFIG_COUNT" not in kwargs["env"]
            assert kwargs["env"]["GIT_CONFIG_GLOBAL"] == os.devnull
            assert "protocol.file.allow=never" in args
            assert "protocol.ext.allow=never" in args
            assert f"core.hooksPath={os.devnull}" in args


@pytest.mark.parametrize("url", [
    "http://github.com/example/service/pull/12", "https://evil.example/example/service/pull/12",
    "https://github.com.evil.example/example/service/pull/12", "https://github.com:443/example/service/pull/12",
    "https://token@github.com/example/service/pull/12", "https://github.com/example/service/pull/12?foo=bar",
    "https://github.com/example/service/pull/12#issuecomment", "https://github.com/example/service/pull/0",
    "https://github.com/example/../pull/12", "https://github.com/example/service/pull/12/commits",
    "https://github.com/-shell/service/pull/12", "https://github.com/example/%2e%2e/pull/12",
])
def test_invalid_url_is_rejected_before_network(monkeypatch, tmp_path, url):
    monkeypatch.setattr(github, "_run", lambda *a, **k: pytest.fail("network should not run"))
    with pytest.raises(github.GitHubError, match="Expected"):
        github.fetch_pr(url, tmp_path / "snapshot")


def test_existing_content_and_path_traversal_are_preserved(monkeypatch, tmp_path):
    monkeypatch.setattr(github, "_run", lambda *a, **k: pytest.fail("network should not run"))
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "important.txt").write_text("preserve me")
    for path in (existing, tmp_path / ".." / "escape"):
        with pytest.raises(github.GitHubError):
            github.fetch_pr("https://github.com/example/service/pull/12", path)
    assert (existing / "important.txt").read_text() == "preserve me"


def test_symlink_parent_rejected(monkeypatch, tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(github, "_run", lambda *a, **k: pytest.fail("network should not run"))
    with pytest.raises(github.GitHubError, match="symlinks"):
        github.fetch_pr("https://github.com/example/service/pull/12", link / "snapshot")


@pytest.mark.parametrize("field", ["base_sha", "head_sha", "repo", "description"])
def test_changed_pr_snapshot_is_rejected(monkeypatch, tmp_path, field):
    after = copy.deepcopy(metadata())
    if field.endswith("_sha"):
        after[field.removesuffix("_sha")]["sha"] = "c" * 40
    elif field == "repo":
        after["head"]["repo"]["id"] = 30
    else:
        after["body"] = "Changed meaning during fetch"
    mock_remote(monkeypatch, after=after)
    with pytest.raises(github.GitHubError, match="changed during fetch"):
        github.fetch_pr("https://github.com/example/service/pull/12", tmp_path / "snapshot")


def test_git_object_mismatch_is_rejected(monkeypatch, tmp_path):
    mock_remote(monkeypatch, object_sha="c" * 40)
    with pytest.raises(github.GitHubError, match="immutable"):
        github.fetch_pr("https://github.com/example/service/pull/12", tmp_path / "snapshot")


def test_untrusted_metadata_cannot_select_remote_or_git_revision(monkeypatch, tmp_path):
    bad = metadata()
    bad["head"]["sha"] = "--upload-pack=malicious"
    monkeypatch.setattr(github, "_run", lambda *a, **k: json.dumps(bad))
    with pytest.raises(github.GitHubError, match="malformed"):
        github.fetch_pr("https://github.com/example/service/pull/12", tmp_path / "snapshot")


def test_subprocess_errors_never_echo_credential_bearing_stderr(monkeypatch):
    monkeypatch.setattr(github.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="", stderr="https://secret-token@github.com/private"))
    with pytest.raises(github.GitHubError) as error:
        github._run(["git", "fetch"])
    assert "secret-token" not in str(error.value)


def test_subprocess_missing_and_timeout_are_actionable(monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr(github.subprocess, "run", missing)
    with pytest.raises(github.GitHubError, match="not installed"):
        github._run(["gh", "api"])
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("git", 1)
    monkeypatch.setattr(github.subprocess, "run", timeout)
    with pytest.raises(github.GitHubError, match="timed out"):
        github._run(["git", "fetch"])
