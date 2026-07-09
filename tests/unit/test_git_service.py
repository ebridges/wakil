import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from wakil.app.git_service import (
    GitServiceError,
    commit_ingest,
    commit_message,
    ensure_clean_for_branch,
    ingest_branch_name,
    start_ingest_branch,
)
from wakil.app.workspace_service import init_workspace, open_session
from wakil.config.settings import WorkspaceConfig
from wakil.integrations import git
from wakil.storage.schema import GitChange


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def git_kb(kb_path: Path) -> WorkspaceConfig:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=kb_path, check=True)
    _git(kb_path, "config", "user.email", "test@example.com")
    _git(kb_path, "config", "user.name", "Test User")
    _git(kb_path, "add", "-A")
    _git(kb_path, "commit", "-q", "-m", "seed")
    init_workspace(kb_path)
    return WorkspaceConfig.load(kb_path)


def test_commit_message_conventions():
    assert commit_message("ingest", "add transcript") == "wakil ingest: add transcript"
    with pytest.raises(GitServiceError):
        commit_message("bogus", "x")


def test_ingest_branch_name_dedupes(git_kb):
    root = git_kb.root_path
    first = ingest_branch_name(root, "Claims Kickoff")
    git.create_branch(root, first)
    second = ingest_branch_name(root, "Claims Kickoff")
    assert second == f"{first}-1"


def test_ensure_clean_rejects_dirty_tree(git_kb):
    (git_kb.root_path / "README.md").write_text("# changed\n")
    with pytest.raises(GitServiceError, match="uncommitted changes"):
        ensure_clean_for_branch(git_kb)


def test_ensure_clean_rejects_non_repo(kb_path):
    init_workspace(kb_path)
    config = WorkspaceConfig.load(kb_path)
    with pytest.raises(GitServiceError, match="not a git repository"):
        ensure_clean_for_branch(config)


def test_start_branch_and_commit_ingest(git_kb):
    root = git_kb.root_path
    branch = start_ingest_branch(git_kb, "Claims Kickoff")
    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD") == branch

    (root / "sources").mkdir(exist_ok=True)
    new_file = root / "sources" / "capture.md"
    new_file.write_text("captured\n")

    outcome = commit_ingest(
        git_kb, ["sources/capture.md"], "Claims Kickoff", "A summary.", branch=branch
    )
    assert outcome.commit_sha == _git(root, "rev-parse", "HEAD")
    assert outcome.message.startswith("wakil ingest: add Claims Kickoff")
    subject = _git(root, "log", "-1", "--format=%s")
    assert subject == "wakil ingest: add Claims Kickoff"
    body = _git(root, "log", "-1", "--format=%b")
    assert "A summary." in body

    with open_session(git_kb) as session:
        change = session.scalar(select(GitChange))
        assert change.operation == "ingest-commit"
        assert change.branch_name == branch
        assert change.commit_sha == outcome.commit_sha


def test_commit_only_stages_named_files(git_kb):
    root = git_kb.root_path
    (root / "unrelated.md").write_text("# user's own work in progress\n")
    (root / "drafts" / "capture.md").write_text("captured\n")

    commit_ingest(git_kb, ["drafts/capture.md"], "Capture", None)
    status = _git(root, "status", "--porcelain")
    assert "unrelated.md" in status  # untouched
    assert "capture.md" not in status  # committed


def test_commit_ingest_requires_files(git_kb):
    with pytest.raises(GitServiceError, match="Nothing to commit"):
        commit_ingest(git_kb, [], "Empty", None)


def test_pr_requires_branch_and_gh(git_kb, monkeypatch):
    root = git_kb.root_path
    (root / "drafts" / "c.md").write_text("x\n")
    with pytest.raises(GitServiceError, match="requires --branch"):
        commit_ingest(git_kb, ["drafts/c.md"], "C", None, branch=None, open_pr=True)

    monkeypatch.setattr("wakil.app.git_service.gh_available", lambda: False)
    (root / "drafts" / "d.md").write_text("x\n")
    with pytest.raises(GitServiceError, match="GitHub CLI"):
        commit_ingest(git_kb, ["drafts/d.md"], "D", None, branch="wakil/ingest/x", open_pr=True)


def test_pr_flow_pushes_and_creates(git_kb, monkeypatch):
    root = git_kb.root_path
    _git(root, "remote", "add", "origin", "https://example.com/repo.git")
    branch = start_ingest_branch(git_kb, "PR Test")
    (root / "drafts" / "pr.md").write_text("x\n")

    calls = {}
    monkeypatch.setattr("wakil.app.git_service.gh_available", lambda: True)
    monkeypatch.setattr(
        "wakil.app.git_service.git.push_branch",
        lambda r, name: calls.setdefault("pushed", name),
    )
    monkeypatch.setattr(
        "wakil.app.git_service.create_pull_request",
        lambda r, title, body: (
            calls.setdefault("pr", (title, body)) and "https://pr.url/1" or "https://pr.url/1"
        ),
    )

    outcome = commit_ingest(
        git_kb, ["drafts/pr.md"], "PR Test", "Summary.", branch=branch, open_pr=True
    )
    assert calls["pushed"] == branch
    assert "wakil ingest: add PR Test" in calls["pr"][0]
    assert outcome.pr_url == "https://pr.url/1"
