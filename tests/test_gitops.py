import subprocess

import pytest

from branchlab.gitops import _safe_path, diff_context, export_revision, resolve_revision


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args]).decode().strip()


@pytest.fixture
def repo(tmp_path):
    directory = tmp_path / "repo"
    directory.mkdir()
    git(directory, "init", "-q")
    git(directory, "config", "user.name", "Test")
    git(directory, "config", "user.email", "test@example.invalid")
    (directory / "service.py").write_text("import storage\nRESULT = 1\n")
    (directory / "storage.py").write_text("CACHE = {}\n")
    (directory / "CONTRACT.md").write_text("Results are stable.\n")
    (directory / ".env").write_text("PASSWORD=not-context\n")
    git(directory, "add", ".")
    git(directory, "commit", "-qm", "base")
    return directory


def test_export_immutable_commit_not_worktree_and_no_hooks(repo, tmp_path):
    sha = resolve_revision(repo, "HEAD")
    marker = tmp_path / "hook-ran"
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    (repo / "service.py").write_text("changed working copy\n")
    destination = tmp_path / "snapshot"
    export_revision(repo, sha, destination)
    assert (destination / "service.py").read_text() == "import storage\nRESULT = 1\n"
    assert not marker.exists()
    assert not (destination / ".git").exists()
    with pytest.raises(ValueError, match="new or empty"):
        export_revision(repo, sha, destination)


def test_context_excludes_secrets_and_has_real_import_edges(repo):
    base = resolve_revision(repo, "HEAD")
    (repo / "service.py").write_text("import storage\nRESULT = 2\n")
    (repo / ".env").write_text("PASSWORD=new-hidden-value\n")
    (repo / "binary.dat").write_bytes(b"\0secret-bytes")
    (repo / "config.py").write_text('KEY = "sk-' + "a" * 35 + '"\n')
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "head")
    result = diff_context(repo, base, resolve_revision(repo, "HEAD"))
    assert result["files"]["CONTRACT.md"]["head"] == "Results are stable.\n"
    assert "-RESULT = 1" in result["diff"]
    assert "+RESULT = 2" in result["diff"]
    assert ".env" not in result["files"]
    assert "config.py" not in result["files"]
    assert "binary.dat" not in result["files"]
    assert "new-hidden-value" not in str(result)
    assert {"source": "service.py", "target": "storage.py", "kind": "imports"} in result["impact_graph"]["edges"]


@pytest.mark.parametrize("path", ["../escape", "/etc/passwd", "a/../../escape", ".git/config", "a/.GIT/config", "a\\..\\b", "a//b", "a/./b", "a\nb"])
def test_path_traversal_is_rejected(path):
    with pytest.raises(ValueError, match="Unsafe"):
        _safe_path(path)


def test_symlink_commit_rejected_before_export(repo, tmp_path):
    (repo / "escape").symlink_to("/etc/passwd")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "unsafe")
    destination = tmp_path / "export"
    with pytest.raises(ValueError, match="Symlinks"):
        export_revision(repo, resolve_revision(repo, "HEAD"), destination)
    assert not destination.exists()


def test_destination_symlink_rejected(repo, tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        export_revision(repo, resolve_revision(repo, "HEAD"), alias / "snapshot")


def test_revision_option_rejected(repo):
    with pytest.raises(ValueError, match="Invalid revision"):
        resolve_revision(repo, "--help")


def test_submodule_tree_rejected(repo, tmp_path):
    sha = resolve_revision(repo, "HEAD")
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},nested")
    git(repo, "commit", "-qm", "gitlink")
    with pytest.raises(ValueError, match="submodules"):
        export_revision(repo, resolve_revision(repo, "HEAD"), tmp_path / "snapshot")
