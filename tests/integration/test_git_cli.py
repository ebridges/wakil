import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wakil.cli.main import app

runner = CliRunner()


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def git_kb(kb_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=kb_path, check=True)
    _git(kb_path, "config", "user.email", "test@example.com")
    _git(kb_path, "config", "user.name", "Test User")
    _git(kb_path, "add", "-A")
    _git(kb_path, "commit", "-q", "-m", "seed")
    runner.invoke(app, ["init", str(kb_path)])
    return kb_path


def test_ingest_with_branch_commits_on_wakil_branch(git_kb, monkeypatch):
    monkeypatch.setattr("wakil.llm.client.resolve_client", lambda: None)
    transcript = git_kb / "meeting.txt"
    transcript.write_text("We approved the routing prototype.\n")
    # The transcript itself would dirty the tree; keep it out of the repo's eyes.
    (git_kb / ".gitignore").write_text("meeting.txt\n")
    _git(git_kb, "add", ".gitignore")
    _git(git_kb, "commit", "-q", "-m", "ignore scratch")

    result = runner.invoke(
        app, ["-w", str(git_kb), "ingest", "transcript", str(transcript), "--yes", "--branch"]
    )
    assert result.exit_code == 0, result.output
    assert "Created branch" in result.output
    assert "Committed" in result.output

    branch = _git(git_kb, "rev-parse", "--abbrev-ref", "HEAD")
    assert branch.startswith("wakil/ingest/")
    assert _git(git_kb, "log", "-1", "--format=%s").startswith("wakil ingest: add")
    assert _git(git_kb, "status", "--porcelain") == ""


def test_ingest_branch_refuses_dirty_tree(git_kb, monkeypatch):
    monkeypatch.setattr("wakil.llm.client.resolve_client", lambda: None)
    (git_kb / "README.md").write_text("# dirty edit\n")
    transcript = git_kb / "meeting.txt"
    transcript.write_text("Some notes.\n")

    result = runner.invoke(
        app, ["-w", str(git_kb), "ingest", "transcript", str(transcript), "--yes", "--branch"]
    )
    assert result.exit_code == 1
    assert "uncommitted changes" in result.output
    # Nothing was ingested.
    assert not list((git_kb / "sources" / "transcripts").glob("2*.md"))


def test_ingest_commit_on_current_branch(git_kb, monkeypatch):
    monkeypatch.setattr("wakil.llm.client.resolve_client", lambda: None)
    transcript = git_kb / "meeting.txt"
    transcript.write_text("Notes for commit flow.\n")
    (git_kb / ".gitignore").write_text("meeting.txt\n")
    _git(git_kb, "add", ".gitignore")
    _git(git_kb, "commit", "-q", "-m", "ignore scratch")

    result = runner.invoke(
        app, ["-w", str(git_kb), "ingest", "transcript", str(transcript), "--yes", "--commit"]
    )
    assert result.exit_code == 0, result.output
    assert _git(git_kb, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert _git(git_kb, "log", "-1", "--format=%s").startswith("wakil ingest: add")


def test_git_summary(git_kb):
    result = runner.invoke(app, ["-w", str(git_kb), "git", "summary"])
    assert result.exit_code == 0
    assert "Branch:" in result.output
    assert "Recent commits" in result.output


def test_git_summary_outside_repo(kb_path):
    runner.invoke(app, ["init", str(kb_path)])
    result = runner.invoke(app, ["-w", str(kb_path), "git", "summary"])
    assert result.exit_code == 1
    assert "not a git repository" in result.output


def test_git_history(git_kb):
    result = runner.invoke(app, ["-w", str(git_kb), "git", "history", "README.md"])
    assert result.exit_code == 0
    assert "seed" in result.output

    result = runner.invoke(app, ["-w", str(git_kb), "git", "history", "no-such-file.md"])
    assert result.exit_code == 0
    assert "No git history" in result.output
