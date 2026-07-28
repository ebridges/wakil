import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from wakil.app.git_service import (
    GitServiceError,
    abandon_landing,
    commit_message,
    ensure_clean_for_branch,
    ingest_branch_name,
    land_ingestion,
    prepare_landing,
)
from wakil.app.workspace_service import init_workspace, open_session
from wakil.config.settings import WorkspaceConfig
from wakil.integrations import git
from wakil.storage.schema import GitChange, Source, Workspace


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def git_kb(kb_path: Path) -> WorkspaceConfig:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=kb_path, check=True)
    _git(kb_path, "config", "user.email", "test@example.com")
    _git(kb_path, "config", "user.name", "Test User")
    _git(kb_path, "config", "commit.gpgsign", "false")
    _git(kb_path, "add", "-A")
    _git(kb_path, "commit", "-q", "-m", "seed")
    init_workspace(kb_path)
    return WorkspaceConfig.load(kb_path)


def _add_local_origin(root: Path) -> None:
    """A local bare repo as 'origin' -- real git operations (fetch/push)
    without any actual network I/O, unlike a fake https:// remote."""
    bare = root.parent / "origin.git"
    if not bare.exists():
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        _git(root, "remote", "add", "origin", str(bare))
    _git(root, "push", "-q", "origin", "main")


def _insert_source(config: WorkspaceConfig, title: str = "Test Source") -> int:
    with open_session(config) as session:
        workspace_id = session.scalar(
            select(Workspace.id).where(Workspace.root_path == str(config.root_path))
        )
        source = Source(workspace_id=workspace_id, source_type="text", title=title)
        session.add(source)
        session.commit()
        return source.id


def test_commit_message_conventions():
    assert commit_message("ingest", "add transcript") == "🧠 wakil ingest: add transcript"
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


def test_resolve_default_branch_independent_of_current_checkout(git_kb):
    root = git_kb.root_path
    git.create_branch(root, "unrelated-work")
    assert git.resolve_default_branch(root) == "main"


def test_worktree_anchors_not_a_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert git.worktree_anchors(plain) is None


def test_worktree_anchors_main_worktree(git_kb):
    root = git_kb.root_path
    anchors = git.worktree_anchors(root)
    assert anchors is not None
    assert anchors.toplevel == root.resolve()
    assert anchors.common_dir == (root / ".git").resolve()


def test_worktree_anchors_shared_across_linked_worktree(git_kb, tmp_path):
    root = git_kb.root_path
    worktree = tmp_path / "linked"
    _git(root, "worktree", "add", "-q", "-b", "scratch/linked", str(worktree), "main")

    main_anchors = git.worktree_anchors(root)
    linked_anchors = git.worktree_anchors(worktree)
    assert main_anchors is not None
    assert linked_anchors is not None
    assert linked_anchors.toplevel == worktree.resolve()
    # The whole point: the common-dir is identical across worktrees, even
    # though each has its own top-level directory.
    assert linked_anchors.common_dir == main_anchors.common_dir


def test_prepare_landing_branches_from_default_not_current_head(git_kb):
    """A wakil ingest kicked off while on an unrelated branch must not stack
    the new ingest branch on top of it -- it should branch from main."""
    root = git_kb.root_path
    git.create_branch(root, "unrelated-work")
    (root / "unrelated.md").write_text("unrelated work\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "unrelated-only commit")
    git.checkout(root, "main")

    landing = prepare_landing(git_kb, source_id=None, title="Claims Kickoff", local=False)
    assert landing.branch is not None
    assert landing.original_branch == "main"
    log = _git(root, "log", "--format=%s")
    assert "unrelated-only commit" not in log


def test_prepare_landing_local_skips_git(git_kb):
    landing = prepare_landing(git_kb, source_id=None, title="Scratch", local=True)
    assert landing.local is True
    assert landing.branch is None
    assert _git(git_kb.root_path, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_land_ingestion_requires_files(git_kb):
    landing = prepare_landing(git_kb, source_id=None, title="Empty", local=False)
    with pytest.raises(GitServiceError, match="Nothing to commit"):
        land_ingestion(
            git_kb,
            landing,
            source_id=1,
            files=[],
            title="Empty",
            summary=None,
            ingest_run_id=None,
            kind="source",
            phase="capture",
        )


def test_land_ingestion_commits_only_named_files_and_returns_to_branch(git_kb):
    root = git_kb.root_path
    source_id = _insert_source(git_kb, "Capture Test")

    landing = prepare_landing(git_kb, source_id=None, title="Capture Test", local=False)
    (root / "unrelated.md").write_text("# user's own work in progress\n")
    (root / "drafts").mkdir(exist_ok=True)
    (root / "drafts" / "capture.md").write_text("captured\n")

    outcome = land_ingestion(
        git_kb,
        landing,
        source_id=source_id,
        files=["drafts/capture.md"],
        title="Capture Test",
        summary="A summary.",
        ingest_run_id=None,
        kind="source",
        phase="capture",
    )

    assert landing.branch is not None
    assert outcome.commit_sha == _git(root, "log", "-1", "--format=%H", landing.branch)
    assert outcome.pr_url is None  # no gh/remote configured in this fixture
    assert outcome.returned_to == "main"
    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD") == "main"

    status = _git(root, "status", "--porcelain")
    assert "unrelated.md" in status  # untouched, still uncommitted
    assert "capture.md" not in status  # committed on the ingest branch

    with open_session(git_kb) as session:
        source = session.get(Source, source_id)
        assert source is not None
        assert source.git_branch == landing.branch
        change = session.scalar(select(GitChange))
        assert change is not None
        assert change.operation == "source-commit"
        assert change.branch_name == landing.branch


def test_land_ingestion_pr_is_opportunistic_when_gh_missing(git_kb, monkeypatch):
    root = git_kb.root_path
    _add_local_origin(root)
    source_id = _insert_source(git_kb, "No GH")
    landing = prepare_landing(git_kb, source_id=None, title="No GH", local=False)
    (root / "drafts").mkdir(exist_ok=True)
    (root / "drafts" / "c.md").write_text("x\n")

    monkeypatch.setattr("wakil.app.git_service.gh_available", lambda: False)
    monkeypatch.setattr(
        "wakil.app.git_service.git.push_branch",
        lambda r, name: pytest.fail("should not push when gh is unavailable"),
    )

    outcome = land_ingestion(
        git_kb,
        landing,
        source_id=source_id,
        files=["drafts/c.md"],
        title="No GH",
        summary=None,
        ingest_run_id=None,
        kind="source",
        phase="capture",
    )
    assert outcome.pr_url is None


def test_land_ingestion_opens_draft_pr_on_capture(git_kb, monkeypatch):
    root = git_kb.root_path
    _add_local_origin(root)
    source_id = _insert_source(git_kb, "PR Test")
    landing = prepare_landing(git_kb, source_id=None, title="PR Test", local=False)
    (root / "drafts").mkdir(exist_ok=True)
    (root / "drafts" / "pr.md").write_text("x\n")

    calls = {}
    monkeypatch.setattr("wakil.app.git_service.gh_available", lambda: True)
    monkeypatch.setattr(
        "wakil.app.git_service.git.push_branch",
        lambda r, name: calls.setdefault("pushed", name),
    )

    def _fake_create_pr(r, title, body, draft=False):
        calls["pr"] = (title, body, draft)
        return "https://pr.url/1"

    monkeypatch.setattr("wakil.app.git_service.create_pull_request", _fake_create_pr)

    outcome = land_ingestion(
        git_kb,
        landing,
        source_id=source_id,
        files=["drafts/pr.md"],
        title="PR Test",
        summary="Summary.",
        ingest_run_id=None,
        kind="source",
        phase="capture",
    )
    assert calls["pushed"] == landing.branch
    assert calls["pr"][2] is True  # draft=True on capture
    assert outcome.pr_url == "https://pr.url/1"

    with open_session(git_kb) as session:
        source = session.get(Source, source_id)
        assert source is not None
        assert source.git_pr_url == "https://pr.url/1"


def test_land_ingestion_enrichment_reuses_pr_and_marks_ready(git_kb, monkeypatch):
    """Simulates capture (opens a draft PR) followed by enrichment landing
    on the same source: it must reuse the branch/PR, not open a second one,
    and flip the PR to ready-for-review."""
    root = git_kb.root_path
    _add_local_origin(root)
    source_id = _insert_source(git_kb, "Lifecycle Test")

    monkeypatch.setattr("wakil.app.git_service.gh_available", lambda: True)
    monkeypatch.setattr("wakil.app.git_service.git.push_branch", lambda r, name: None)
    monkeypatch.setattr(
        "wakil.app.git_service.create_pull_request",
        lambda r, title, body, draft=False: "https://pr.url/42",
    )

    capture_landing = prepare_landing(git_kb, source_id=None, title="Lifecycle Test", local=False)
    (root / "sources").mkdir(exist_ok=True)
    (root / "sources" / "raw.md").write_text("raw\n")
    capture_outcome = land_ingestion(
        git_kb,
        capture_landing,
        source_id=source_id,
        files=["sources/raw.md"],
        title="Lifecycle Test",
        summary=None,
        ingest_run_id=None,
        kind="source",
        phase="capture",
    )
    assert capture_outcome.pr_url == "https://pr.url/42"

    calls = {}
    monkeypatch.setattr(
        "wakil.app.git_service.mark_pull_request_ready",
        lambda r, pr_url: calls.setdefault("ready", pr_url),
    )
    monkeypatch.setattr(
        "wakil.app.git_service.comment_on_pull_request",
        lambda r, pr_url, body: calls.setdefault("comment", (pr_url, body)),
    )

    enrich_landing = prepare_landing(
        git_kb, source_id=source_id, title="Lifecycle Test", local=False
    )
    assert enrich_landing.branch == capture_landing.branch  # resumed, not a new branch

    (root / "drafts").mkdir(exist_ok=True)
    (root / "drafts" / "note.md").write_text("enriched\n")
    enrich_outcome = land_ingestion(
        git_kb,
        enrich_landing,
        source_id=source_id,
        files=["drafts/note.md"],
        title="Lifecycle Test",
        summary="Entities resolved.",
        ingest_run_id=None,
        kind="ingest",
        phase="enrichment",
    )

    assert enrich_outcome.branch == capture_landing.branch
    assert enrich_outcome.pr_url == "https://pr.url/42"  # same PR, not a new one
    assert calls["ready"] == "https://pr.url/42"
    assert calls["comment"][0] == "https://pr.url/42"
    assert "Enrichment landed." in calls["comment"][1]


def test_resume_source_branch_fetches_from_origin_when_deleted_locally(git_kb, monkeypatch):
    """Simulates capture happening in one session and enrichment resuming
    in another where the branch was never fetched locally -- it must fetch
    it from origin rather than creating a fresh one."""
    root = git_kb.root_path
    _add_local_origin(root)

    source_id = _insert_source(git_kb, "Cross Session")
    landing = prepare_landing(git_kb, source_id=None, title="Cross Session", local=False)
    branch = landing.branch
    assert branch is not None
    (root / "drafts").mkdir(exist_ok=True)
    (root / "drafts" / "raw.md").write_text("raw\n")
    monkeypatch.setattr("wakil.app.git_service.gh_available", lambda: False)
    land_ingestion(
        git_kb,
        landing,
        source_id=source_id,
        files=["drafts/raw.md"],
        title="Cross Session",
        summary=None,
        ingest_run_id=None,
        kind="source",
        phase="capture",
    )

    # The branch itself needs to exist on origin for a "different session"
    # to be able to resume it -- push it explicitly (gh was mocked off
    # above, so land_ingestion's own push-on-PR path never ran).
    _git(root, "push", "-q", "origin", branch)

    # Simulate a fresh checkout that never saw the branch locally.
    _git(root, "switch", "main")
    _git(root, "branch", "-D", branch)
    assert not git.branch_exists(root, branch)

    resumed = prepare_landing(git_kb, source_id=source_id, title="Cross Session", local=False)
    assert resumed.branch == branch


def test_resume_source_branch_starts_revision_when_branch_gone_everywhere(git_kb):
    """If the branch is gone both locally and on origin (its PR was merged
    and the branch deleted), start a follow-up branch instead of erroring."""
    root = git_kb.root_path
    with open_session(git_kb) as session:
        workspace_id = session.scalar(
            select(Workspace.id).where(Workspace.root_path == str(git_kb.root_path))
        )
        source = Source(
            workspace_id=workspace_id,
            source_type="text",
            title="Gone Branch",
            git_branch="wakil/ingest/2026-01-01-gone-branch",
        )
        session.add(source)
        session.commit()
        source_id = source.id

    landing = prepare_landing(git_kb, source_id=source_id, title="Gone Branch", local=False)
    assert landing.branch is not None
    assert landing.branch != "wakil/ingest/2026-01-01-gone-branch"
    assert "revision" in landing.branch
    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD") == landing.branch


def test_abandon_landing_returns_to_original_branch(git_kb):
    landing = prepare_landing(git_kb, source_id=None, title="Nothing To Write", local=False)
    assert _git(git_kb.root_path, "rev-parse", "--abbrev-ref", "HEAD") == landing.branch
    abandon_landing(git_kb, landing)
    assert _git(git_kb.root_path, "rev-parse", "--abbrev-ref", "HEAD") == "main"
