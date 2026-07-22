import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wakil.cli.main import app

runner = CliRunner()

CAPTURE_METADATA_JSON = json.dumps(
    {
        "title": "2026-07-09 Fake Capture Title",
        "abstract": "A fake abstract for git-landing tests, roughly the length a real one "
        "would be.",
    }
)


class FakeCaptureClient:
    """The capture-time title/abstract call: one scripted CaptureMetadata payload."""

    model = "fake-model"

    def __init__(self):
        self.queue = [CAPTURE_METADATA_JSON]

    def complete(self, system, prompt, max_tokens=8192):
        return self.queue.pop(0)


def _client_queue(monkeypatch, *clients):
    """Patch resolve_client to hand back each client in order, one per call --
    capture and enrich each resolve a client independently."""
    it = iter(clients)
    monkeypatch.setattr("wakil.llm.client.resolve_client", lambda: next(it))


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def git_kb(kb_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=kb_path, check=True)
    _git(kb_path, "config", "user.email", "test@example.com")
    _git(kb_path, "config", "user.name", "Test User")
    _git(kb_path, "config", "commit.gpgsign", "false")
    _git(kb_path, "add", "-A")
    _git(kb_path, "commit", "-q", "-m", "seed")
    runner.invoke(app, ["init", str(kb_path)])
    return kb_path


def test_ingest_lands_on_a_branch_and_returns_to_main_by_default(git_kb, monkeypatch):
    _client_queue(monkeypatch, FakeCaptureClient())
    monkeypatch.setattr("wakil.app.git_service.gh_available", lambda: False)
    transcript = git_kb / "meeting.txt"
    transcript.write_text("We approved the routing prototype.\n")
    # The transcript itself would dirty the tree; keep it out of the repo's eyes.
    (git_kb / ".gitignore").write_text("meeting.txt\n")
    _git(git_kb, "add", ".gitignore")
    _git(git_kb, "commit", "-q", "-m", "ignore scratch")

    result = runner.invoke(
        app, ["-w", str(git_kb), "ingest", "transcript", str(transcript), "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert "On branch" in result.output
    assert "Committed" in result.output
    assert "Returned to main" in result.output

    # Landing returns the session to where it started -- not left on the
    # ingest branch, since it may be revisited later by an unrelated command.
    assert _git(git_kb, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    branches = _git(git_kb, "branch", "--list", "wakil/ingest/*")
    assert "wakil/ingest/" in branches
    log = _git(git_kb, "log", "--all", "--format=%s")
    assert "wakil source: add" in log


def test_ingest_branch_refuses_dirty_tree(git_kb, monkeypatch):
    _client_queue(monkeypatch, FakeCaptureClient())
    (git_kb / "README.md").write_text("# dirty edit\n")
    transcript = git_kb / "meeting.txt"
    transcript.write_text("Some notes.\n")

    result = runner.invoke(
        app, ["-w", str(git_kb), "ingest", "transcript", str(transcript), "--yes"]
    )
    assert result.exit_code == 1
    assert "uncommitted changes" in result.output
    # Nothing was ingested.
    assert not list((git_kb / "sources" / "transcripts").glob("2*.md"))


def test_ingest_local_skips_git_entirely(git_kb, monkeypatch):
    _client_queue(monkeypatch, FakeCaptureClient())
    transcript = git_kb / "meeting.txt"
    transcript.write_text("Notes for local flow.\n")
    (git_kb / ".gitignore").write_text("meeting.txt\n")
    _git(git_kb, "add", ".gitignore")
    _git(git_kb, "commit", "-q", "-m", "ignore scratch")

    result = runner.invoke(
        app, ["-w", str(git_kb), "ingest", "transcript", str(transcript), "--yes", "--local"]
    )
    assert result.exit_code == 0, result.output
    assert "not committed" in result.output
    assert _git(git_kb, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert _git(git_kb, "status", "--porcelain") != ""  # captured file left uncommitted


def test_ingest_returns_to_original_branch_when_apply_fails(git_kb, monkeypatch):
    """If apply_capture fails after prepare_landing already switched onto
    the throwaway ingest branch -- e.g. it lost the content-hash dedup
    race with a concurrent identical capture -- the session must return to
    its original branch rather than being left stranded."""
    from wakil.app.ingest_service import IngestError

    _client_queue(monkeypatch, FakeCaptureClient())
    transcript = git_kb / "meeting.txt"
    transcript.write_text("Some notes.\n")
    (git_kb / ".gitignore").write_text("meeting.txt\n")
    _git(git_kb, "add", ".gitignore")
    _git(git_kb, "commit", "-q", "-m", "ignore scratch")

    def _lost_the_race(config, proposal):
        raise IngestError("Source already ingested (source id 1); lost a race")

    monkeypatch.setattr("wakil.app.ingest_service.apply_capture", _lost_the_race)

    result = runner.invoke(
        app, ["-w", str(git_kb), "ingest", "transcript", str(transcript), "--yes"]
    )
    assert result.exit_code == 1
    assert "lost a race" in result.output
    assert _git(git_kb, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert _git(git_kb, "status", "--porcelain") == ""


def test_enrich_lands_on_the_same_branch_capture_started(git_kb, monkeypatch):
    """Capture then enrich the same source: they must share one branch, not
    open two disconnected ones."""
    extraction = json.dumps(
        {
            "title": "Routing Sync",
            "summary": "A short meeting about routing.",
            "key_points": ["Routing prototype approved"],
            "memories": [],
            "relationships": [],
            "proposed_note": {
                "path": "meetings/2026/2026-07-09-routing.md",
                "markdown": (
                    "---\ntype: meeting\ntitle: Routing\ndate: 2026-07-09\n"
                    "created: 2026-07-09\n---\n\n# Routing\n"
                ),
            },
        }
    )
    resolution = json.dumps({"entities": []})

    class FakeClient:
        model = "fake-model"

        def __init__(self):
            self.queue = [extraction, resolution]

        def complete(self, system, prompt, max_tokens=8192):
            return self.queue.pop(0)

    _client_queue(monkeypatch, FakeCaptureClient(), FakeClient())
    monkeypatch.setattr("wakil.app.git_service.gh_available", lambda: False)
    transcript = git_kb / "meeting.txt"
    transcript.write_text("We approved the routing prototype.\n")
    (git_kb / ".gitignore").write_text("meeting.txt\n")
    _git(git_kb, "add", ".gitignore")
    _git(git_kb, "commit", "-q", "-m", "ignore scratch")

    capture = runner.invoke(
        app, ["-w", str(git_kb), "ingest", "transcript", str(transcript), "--yes"]
    )
    assert capture.exit_code == 0, capture.output
    branches_after_capture = _git(git_kb, "branch", "--list", "wakil/ingest/*").splitlines()
    assert len(branches_after_capture) == 1

    enrich = runner.invoke(app, ["-w", str(git_kb), "enrich", "1", "--yes"])
    assert enrich.exit_code == 0, enrich.output
    branches_after_enrich = _git(git_kb, "branch", "--list", "wakil/ingest/*").splitlines()
    # Still exactly one wakil/ingest branch -- enrichment resumed it, it
    # didn't create a second one.
    assert len(branches_after_enrich) == 1
    assert _git(git_kb, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    log = _git(git_kb, "log", "--all", "--format=%s")
    assert "wakil source: add" in log
    assert "wakil ingest: add" in log


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
