"""The lock is only useful if the command paths actually take it.

`tests/unit/test_locking.py` covers the primitive. These cover the wiring:
before these existed, deleting a `with _workspace_git_lock(...)` from
`cli/main.py` left the whole suite green.
"""

import contextlib
import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wakil.app.workspace_service import init_workspace
from wakil.cli.main import app
from wakil.config.settings import WorkspaceConfig
from wakil.mcp.proposals import ProposalCache, ProposalNotFoundError

runner = CliRunner()

CAPTURE_METADATA_JSON = json.dumps({"title": "Test Capture", "abstract": "An abstract."})


class _FakeCaptureClient:
    model = "fake-model"

    def __init__(self):
        self.queue = [CAPTURE_METADATA_JSON]

    def complete(self, system, prompt, max_tokens=8192, *, cacheable_prefix=None):
        assert self.queue, "ran out of scripted responses"
        return self.queue.pop(0)


@pytest.fixture
def recorder(monkeypatch):
    """Replace the lock with a context manager that records acquire/release
    against the operations it is meant to bracket."""
    events: list[str] = []

    @contextlib.contextmanager
    def _fake_lock(config, *, local: bool):
        if local:
            events.append("skipped(local)")
            yield
            return
        events.append("acquired")
        try:
            yield
        finally:
            events.append("released")

    monkeypatch.setattr("wakil.cli.main._workspace_git_lock", _fake_lock)
    return events


def _git_kb(kb_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=kb_path, check=True)
    for args in (
        ("config", "user.email", "t@example.com"),
        ("config", "user.name", "T"),
        ("config", "commit.gpgsign", "false"),
        ("add", "-A"),
        ("commit", "-q", "-m", "seed"),
    ):
        subprocess.run(["git", "-C", str(kb_path), *args], check=True, capture_output=True)
    runner.invoke(app, ["init", str(kb_path)])
    return kb_path


def test_ingest_takes_the_lock_before_resolving_a_branch(kb_path, monkeypatch, recorder):
    monkeypatch.setattr("wakil.llm.client.resolve_client", lambda: _FakeCaptureClient())
    _git_kb(kb_path)
    transcript = kb_path.parent / "meeting.txt"  # outside the tree being branched
    transcript.write_text("We approved the prototype.\n")

    from wakil.app import git_service

    real = git_service.prepare_landing

    def spy(*a, **k):
        recorder.append("prepare_landing")
        return real(*a, **k)

    monkeypatch.setattr(git_service, "prepare_landing", spy)

    result = runner.invoke(
        app, ["-w", str(kb_path), "ingest", "transcript", str(transcript), "--yes"]
    )
    assert result.exit_code == 0, result.output
    # The checkout must already be ours before a branch is resolved.
    assert recorder.index("acquired") < recorder.index("prepare_landing")
    assert recorder[-1] == "released"


def test_local_ingest_does_not_contend(kb_path, monkeypatch, recorder):
    """ADR 0021 claims `--local` doesn't take the lock; nothing checked it."""
    monkeypatch.setattr("wakil.llm.client.resolve_client", lambda: _FakeCaptureClient())
    init_workspace(kb_path)
    transcript = kb_path.parent / "meeting.txt"
    transcript.write_text("We approved the prototype.\n")

    result = runner.invoke(
        app, ["-w", str(kb_path), "ingest", "transcript", str(transcript), "--yes", "--local"]
    )
    assert result.exit_code == 0, result.output
    assert recorder == ["skipped(local)"]


def test_schema_migrate_takes_the_lock(kb_path, monkeypatch, recorder):
    """A vault-wide frontmatter rewrite is exactly the write that raced
    `apply_enrichment` in #182, and it was running unlocked."""
    _git_kb(kb_path)
    (kb_path / "people" / "jane-doe.md").write_text(
        "---\ntype: person\nname: Jane Doe\nlink: https://example.com/jane\n---\n\n# Jane Doe\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["-w", str(kb_path), "schema", "migrate", "--yes"])
    assert result.exit_code == 0, result.output
    assert "acquired" in recorder
    assert recorder[-1] == "released"


# --- MCP: a transient failure must not consume the proposal ----------------


def test_peek_leaves_the_proposal_for_a_retry():
    cache = ProposalCache()
    pid = cache.put("enrichment", {"payload": 1})
    assert cache.peek("enrichment", pid) == {"payload": 1}
    assert cache.peek("enrichment", pid) == {"payload": 1}  # still there
    assert cache.claim("enrichment", pid) == {"payload": 1}
    with pytest.raises(ProposalNotFoundError):
        cache.peek("enrichment", pid)


def test_claim_is_single_use_even_after_two_peeks():
    """Two worker threads can both `peek` the same id while queued on the
    workspace lock. Only one may go on to apply it -- `apply_enrichment`
    rewrites existing notes, so a second application is not idempotent."""
    cache = ProposalCache()
    pid = cache.put("enrichment", {"payload": 1})
    cache.peek("enrichment", pid)  # thread A
    cache.peek("enrichment", pid)  # thread B
    cache.claim("enrichment", pid)  # A wins the lock
    with pytest.raises(ProposalNotFoundError):
        cache.claim("enrichment", pid)  # B must not re-apply


def test_enrich_apply_keeps_the_proposal_when_the_workspace_is_busy(kb_path, monkeypatch):
    """The proposal cost two model calls, and a contended lock is transient,
    so consuming it leaves the agent with retry advice it can't act on."""
    from wakil.app.locking import WorkspaceBusyError
    from wakil.mcp import tools

    init_workspace(kb_path)
    config = WorkspaceConfig.load(kb_path)
    cache = ProposalCache()

    class _Proposal:
        source_id = 1

    proposal_id = cache.put("enrichment", _Proposal())

    @contextlib.contextmanager
    def _busy(config):
        raise WorkspaceBusyError("Another wakil process is using this checkout (pid 999).")
        yield  # pragma: no cover

    # Patch the lock, not `_git_lock_or_tool_error` -- converting
    # WorkspaceBusyError into a ToolError is part of what's under test.
    monkeypatch.setattr(tools, "git_lock", _busy)

    with pytest.raises(tools.ToolError, match="Another wakil process"):
        tools.enrich_apply(config, cache, proposal_id)

    # Still retryable -- the whole point of the change.
    assert cache.peek("enrichment", proposal_id) is not None
