import inspect
import shlex
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from wakil.app.git_service import (
    BranchDriftError,
    GitServiceError,
    abandon_landing,
    assert_landing_intact,
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

    def _fake_create_pr(r, title, body, *, head, base, draft=False):
        calls["pr"] = (title, body, draft)
        calls["head"] = head
        calls["base"] = base
        return "https://pr.url/1"

    monkeypatch.setattr("wakil.app.git_service.create_pull_request", _fake_create_pr)
    monkeypatch.setattr("wakil.app.git_service.find_pull_request", lambda r, head: None)

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
    # gh must be told the head branch explicitly; left to infer it from cwd it
    # opened PRs against whatever happened to be checked out (#180).
    assert calls["head"] == landing.branch
    assert calls["base"] == "main"
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
        lambda r, title, body, *, head, base, draft=False: "https://pr.url/42",
    )
    monkeypatch.setattr("wakil.app.git_service.find_pull_request", lambda r, head: None)

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


# --- commit timeout (issue #171) -------------------------------------------


def test_commit_timeout_defaults_and_honors_the_env_override(monkeypatch):
    monkeypatch.delenv("WAKIL_GIT_COMMIT_TIMEOUT", raising=False)
    assert git.commit_timeout() == git.COMMIT_TIMEOUT_SECONDS

    monkeypatch.setenv("WAKIL_GIT_COMMIT_TIMEOUT", "900")
    assert git.commit_timeout() == 900

    # Junk and non-positive values fall back rather than disabling the guard.
    for bad in ("", "abc", "0", "-5"):
        monkeypatch.setenv("WAKIL_GIT_COMMIT_TIMEOUT", bad)
        assert git.commit_timeout() == git.COMMIT_TIMEOUT_SECONDS


def test_stage_and_commit_gives_the_commit_its_own_timeout(git_kb, monkeypatch):
    """`git commit` may sit waiting on an interactive signing prompt; the
    surrounding add/rev-parse calls may not."""
    monkeypatch.setenv("WAKIL_GIT_COMMIT_TIMEOUT", "777")
    seen: list[tuple[str, int | None]] = []
    real_run = subprocess.run

    def spy(args, **kwargs):
        seen.append((args[3], kwargs.get("timeout")))
        return real_run(args, **kwargs)

    monkeypatch.setattr("wakil.integrations.git.subprocess.run", spy)

    (git_kb.root_path / "note.md").write_text("hi\n", encoding="utf-8")
    git.stage_and_commit(git_kb.root_path, ["note.md"], "test: add note")

    # Compare the neighbours against the default rather than a literal, so a
    # future change to `_run_git_checked`'s default doesn't fail this test for
    # the wrong reason -- what matters is that only `commit` differs.
    default = inspect.signature(git._run_git_checked).parameters["timeout"].default
    by_verb = dict(seen)
    assert by_verb["commit"] == 777
    assert by_verb["add"] == default
    assert by_verb["rev-parse"] == default


def test_commit_timeout_leaves_head_on_the_branch_with_work_staged(git_kb, monkeypatch):
    """On a timed-out commit the caller must be able to finish by hand, so
    wakil must not switch away from the branch (issue #171)."""
    root = git_kb.root_path
    source_id = _insert_source(git_kb, title="Signing Prompt")
    landing = prepare_landing(git_kb, source_id=source_id, title="Signing Prompt", local=False)
    assert landing.branch is not None
    (root / "note.md").write_text("hi\n", encoding="utf-8")

    real_run = subprocess.run

    def timeout_on_commit(args, **kwargs):
        if args[3] == "commit":
            raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs.get("timeout", 0))
        return real_run(args, **kwargs)

    monkeypatch.setattr("wakil.integrations.git.subprocess.run", timeout_on_commit)

    with pytest.raises(GitServiceError) as excinfo:
        land_ingestion(
            git_kb,
            landing,
            source_id=source_id,
            files=["note.md"],
            title="Signing Prompt",
            summary=None,
            ingest_run_id=None,
            kind="ingest",
            phase="capture",
        )

    message = str(excinfo.value)
    assert "timed out" in message
    assert "WAKIL_GIT_COMMIT_TIMEOUT" in message
    # Still on the ingest branch, with the staged change intact.
    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD") == landing.branch
    assert "note.md" in _git(root, "diff", "--cached", "--name-only")


def test_a_killed_commit_leaves_the_repo_usable(git_kb, monkeypatch):
    """Causes a real timeout instead of simulating one.

    `subprocess.run` SIGKILLs the child, and git holds `.git/index.lock`
    across the whole commit, so the killed process used to leave the lock
    behind — wedging every later git write and making the recovery the error
    message advertised fail outright. A slow pre-commit hook stands in for an
    interactive signing prompt: git has already taken the lock by the time it
    runs.
    """
    root = git_kb.root_path
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nsleep 30\n")
    hook.chmod(0o755)
    monkeypatch.setenv("WAKIL_GIT_COMMIT_TIMEOUT", "2")
    (root / "note.md").write_text("hi\n")

    with pytest.raises(git.GitError) as excinfo:
        git.stage_and_commit(root, ["note.md"], "test: a wakil message\n\nWith a body.")

    message = str(excinfo.value)
    assert "timed out" in message
    # No stale locks: the repo is still writable by git.
    assert list((root / ".git").glob("*.lock")) == []
    # And the message the commit would have had is recoverable.
    editmsg = root / ".git" / "COMMIT_EDITMSG"
    assert "a wakil message" in editmsg.read_text()
    # Absolute path: `.git` is a *file* in a linked worktree, so the relative
    # form fails there with "could not read log file: Not a directory".
    assert f"commit -F {editmsg}" in message

    # The advertised recovery actually runs, once the human-paced step is done.
    hook.unlink()
    _git(root, "commit", "-F", str(editmsg))
    assert "a wakil message" in _git(root, "log", "-1", "--format=%s%n%b")


def test_a_timed_out_push_is_not_described_as_a_signing_prompt(git_kb, monkeypatch):
    """The signing framing belongs to `commit`; `_run_git_checked` is also how
    push/add/switch run, and every clause of it is wrong for those."""
    real_run = subprocess.run

    def timeout_on_push(args, **kwargs):
        if args[3] == "push":
            raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs.get("timeout", 0))
        return real_run(args, **kwargs)

    monkeypatch.setattr("wakil.integrations.git.subprocess.run", timeout_on_push)
    with pytest.raises(git.GitError) as excinfo:
        git.push_branch(git_kb.root_path, "some-branch")
    message = str(excinfo.value)
    assert message == "git push timed out after 120 seconds."
    assert "signing" not in message
    assert "WAKIL_GIT_COMMIT_TIMEOUT" not in message


def test_a_commit_message_containing_timed_out_is_not_a_timeout(git_kb):
    """`_run_git_checked` embeds the full argv in its failure text, and that
    argv includes the commit message -- which is model-generated from
    ingested content. Sniffing "timed out" out of that string routed an
    ordinary commit failure into stale-lock cleanup."""
    root = git_kb.root_path
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'hook says no' >&2\nexit 1\n")
    hook.chmod(0o755)
    (root / "note.md").write_text("hi\n")

    with pytest.raises(git.GitError) as excinfo:
        git.stage_and_commit(root, ["note.md"], "📥 wakil source: add The day our API timed out")

    assert not isinstance(excinfo.value, git.GitTimeout)
    message = str(excinfo.value)
    # No timeout advice, and no claim to have cleaned up after a kill.
    assert "WAKIL_GIT_COMMIT_TIMEOUT" not in message
    assert "Removed the stale" not in message


def test_a_lock_held_by_a_live_process_is_never_deleted(git_kb):
    """The stale-lock cleanup is only safe because a real timeout means our
    own child died holding it. Misclassification made it delete a lock some
    other process was actively using."""
    root = git_kb.root_path
    foreign_lock = root / ".git" / "index.lock"
    foreign_lock.write_text("held by a live process\n")
    (root / "note.md").write_text("hi\n")

    with pytest.raises(git.GitError):
        git.stage_and_commit(root, ["note.md"], "📥 wakil source: add A note that timed out")

    assert foreign_lock.exists()
    assert foreign_lock.read_text() == "held by a live process\n"


def test_the_recovery_command_works_in_a_linked_worktree(git_kb, tmp_path, monkeypatch):
    """In a linked worktree `<root>/.git` is a file, so a relative
    `-F .git/COMMIT_EDITMSG` fails with "Not a directory"."""
    linked = tmp_path / "linked"
    _git(git_kb.root_path, "worktree", "add", "-q", "-b", "scratch/wt", str(linked), "main")
    assert (linked / ".git").is_file()  # the thing that breaks the relative form

    # Hooks live in the *common* dir, not the worktree's own git dir.
    hook_dir = Path(_git(linked, "rev-parse", "--git-common-dir")).resolve() / "hooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    hook = hook_dir / "pre-commit"
    hook.write_text("#!/bin/sh\nsleep 30\n")
    hook.chmod(0o755)
    monkeypatch.setenv("WAKIL_GIT_COMMIT_TIMEOUT", "2")
    (linked / "note.md").write_text("hi\n")

    with pytest.raises(git.GitTimeout) as excinfo:
        git.stage_and_commit(linked, ["note.md"], "test: worktree message")

    # Run the advertised command verbatim, rather than reconstructing it --
    # that is the claim under test.
    recovery = next(
        line.strip() for line in str(excinfo.value).splitlines() if "commit -F" in line
    )
    hook.unlink()
    subprocess.run(shlex.split(recovery), check=True, capture_output=True)
    assert "worktree message" in _git(linked, "log", "-1", "--format=%s")


def test_the_recovery_command_does_not_sweep_in_the_users_staged_work(git_kb, monkeypatch):
    """The commit that timed out was pathspec-scoped; an unscoped recovery
    would commit everything in the index."""
    root = git_kb.root_path
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nsleep 30\n")
    hook.chmod(0o755)
    monkeypatch.setenv("WAKIL_GIT_COMMIT_TIMEOUT", "2")

    (root / "MY_PRIVATE_DRAFT.md").write_text("not wakil's to commit\n")
    _git(root, "add", "MY_PRIVATE_DRAFT.md")
    (root / "wakil_note.md").write_text("wakil's file\n")

    with pytest.raises(git.GitTimeout) as excinfo:
        git.stage_and_commit(root, ["wakil_note.md"], "test: scoped")

    recovery = next(
        line.strip() for line in str(excinfo.value).splitlines() if "commit -F" in line
    )
    hook.unlink()
    subprocess.run(shlex.split(recovery), check=True, capture_output=True)

    committed = _git(root, "show", "--name-only", "--format=", "HEAD").split()
    assert committed == ["wakil_note.md"]
    # The user's own staged file is untouched, still staged.
    assert "MY_PRIVATE_DRAFT.md" in _git(root, "diff", "--cached", "--name-only")


# --- landing observes reality instead of asserting it (issues #180, #181) ---


def test_land_ingestion_refuses_when_head_drifted(git_kb):
    """Issue #181: `land_ingestion` committed to whatever HEAD happened to be
    and then *printed* the branch it meant to use, so enrichment output
    landed silently on main."""
    root = git_kb.root_path
    source_id = _insert_source(git_kb, "Drifted")
    landing = prepare_landing(git_kb, source_id=source_id, title="Drifted", local=False)
    assert landing.branch is not None
    (root / "drafts").mkdir(exist_ok=True)
    (root / "drafts" / "d.md").write_text("written\n")

    main_before = _git(root, "rev-parse", "main")
    # Something else moves the working tree -- a concurrent wakil process
    # (#182), or the user.
    _git(root, "switch", "-q", "main")

    with pytest.raises(GitServiceError) as excinfo:
        land_ingestion(
            git_kb,
            landing,
            source_id=source_id,
            files=["drafts/d.md"],
            title="Drifted",
            summary=None,
            ingest_run_id=None,
            kind="source",
            phase="capture",
        )

    message = str(excinfo.value)
    assert landing.branch in message
    assert "HEAD is on 'main'" in message
    # Nothing committed anywhere, and no rescue-checkout under the other process.
    assert _git(root, "rev-parse", "main") == main_before
    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert (root / "drafts" / "d.md").exists()


def test_land_ingestion_returns_to_default_not_the_shell_branch(git_kb):
    """Issue #181 part 2: the return branch was frozen at prepare time, so
    enrich 'returned to' an unrelated branch left over from an earlier
    command."""
    root = git_kb.root_path
    git.create_branch(root, "some-unrelated-branch")
    _git(root, "add", "-A")
    source_id = _insert_source(git_kb, "Return Test")

    landing = prepare_landing(git_kb, source_id=source_id, title="Return Test", local=False)
    assert landing.branch is not None
    (root / "drafts").mkdir(exist_ok=True)
    (root / "drafts" / "r.md").write_text("x\n")
    outcome = land_ingestion(
        git_kb,
        landing,
        source_id=source_id,
        files=["drafts/r.md"],
        title="Return Test",
        summary=None,
        ingest_run_id=None,
        kind="source",
        phase="capture",
    )

    assert outcome.returned_to == "main"
    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    # The commit really is on the ingest branch, not just labelled that way.
    assert outcome.branch == landing.branch
    assert outcome.commit_sha == _git(root, "log", "-1", "--format=%H", landing.branch)
    assert outcome.commit_sha != _git(root, "rev-parse", "main")


def test_ensure_clean_raises_when_status_cannot_be_read(git_kb, monkeypatch):
    """A failed `git status` used to read as 'tree is clean', which lets wakil
    branch and commit on top of the user's uncommitted work."""
    monkeypatch.setattr(
        "wakil.app.git_service.git.status_lines",
        lambda root: (_ for _ in ()).throw(git.GitError("status exploded")),
    )
    with pytest.raises(GitServiceError, match="Could not read the working tree"):
        ensure_clean_for_branch(git_kb)


def test_require_branch_exists_distinguishes_absent_from_unreadable(git_kb, tmp_path):
    root = git_kb.root_path
    assert git.require_branch_exists(root, "main") is True
    assert git.require_branch_exists(root, "no-such-branch") is False
    # Not a repo at all: an answer is not available, so it must not invent one.
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    with pytest.raises(git.GitError):
        git.require_branch_exists(outside, "main")


def test_resume_refuses_to_guess_when_the_branch_check_fails(git_kb, monkeypatch):
    """A false 'no such branch' cuts a *second* branch for a source that
    already had one, and the DB then disagrees with reality (#180)."""
    source_id = _insert_source(git_kb, "Guess Test")
    with open_session(git_kb) as session:
        source = session.get(Source, source_id)
        assert source is not None
        source.git_branch = "wakil/ingest/2026-01-01-guess-test"
        session.commit()

    monkeypatch.setattr(
        "wakil.app.git_service.git.require_branch_exists",
        lambda root, name: (_ for _ in ()).throw(git.GitError("ref check exploded")),
    )
    with pytest.raises(GitServiceError, match="Refusing to guess"):
        prepare_landing(git_kb, source_id=source_id, title="Guess Test", local=False)


def test_create_fresh_branch_requires_a_resolvable_default(git_kb, monkeypatch):
    """Unresolvable default branch used to mean 'branch from current HEAD',
    so a fresh ingest branch could be cut off an unrelated branch."""
    monkeypatch.setattr("wakil.app.git_service.git.resolve_default_branch", lambda root: None)
    branches_before = _git(git_kb.root_path, "branch", "--format=%(refname:short)")
    with pytest.raises(GitServiceError, match="default branch"):
        prepare_landing(git_kb, source_id=None, title="No Default", local=False)
    assert _git(git_kb.root_path, "branch", "--format=%(refname:short)") == branches_before


def test_land_pr_adopts_an_existing_pr_found_by_head(git_kb, monkeypatch):
    """A PR can exist on GitHub without being recorded locally; discovering
    that at `gh pr create` time was a hard failure (#180)."""
    root = git_kb.root_path
    _add_local_origin(root)
    source_id = _insert_source(git_kb, "Adopt Test")
    landing = prepare_landing(git_kb, source_id=source_id, title="Adopt Test", local=False)
    (root / "drafts").mkdir(exist_ok=True)
    (root / "drafts" / "a.md").write_text("x\n")

    seen = {}
    monkeypatch.setattr("wakil.app.git_service.gh_available", lambda: True)
    monkeypatch.setattr("wakil.app.git_service.git.push_branch", lambda r, name: None)
    def _fake_find_pr(r, head):
        seen["head"] = head
        return "https://pr.url/99"

    monkeypatch.setattr("wakil.app.git_service.find_pull_request", _fake_find_pr)
    monkeypatch.setattr(
        "wakil.app.git_service.create_pull_request",
        lambda *a, **k: pytest.fail("must not create a PR when one already exists"),
    )
    monkeypatch.setattr("wakil.app.git_service.comment_on_pull_request", lambda r, url, body: None)

    outcome = land_ingestion(
        git_kb,
        landing,
        source_id=source_id,
        files=["drafts/a.md"],
        title="Adopt Test",
        summary=None,
        ingest_run_id=None,
        kind="source",
        phase="capture",
    )

    assert seen["head"] == landing.branch  # looked up by exact branch, not fuzzily
    assert outcome.pr_url == "https://pr.url/99"
    with open_session(git_kb) as session:
        source = session.get(Source, source_id)
        assert source is not None
        assert source.git_pr_url == "https://pr.url/99"  # backfilled


# --- drift at the push step (issue #195 review, findings 1-3) --------------


def _drift_setup(git_kb, monkeypatch, *, title: str):
    """Land far enough to have a real commit, then move HEAD out from under
    the landing before the push -- the "another process owns this tree" case."""
    root = git_kb.root_path
    _add_local_origin(root)
    source_id = _insert_source(git_kb, title)
    landing = prepare_landing(git_kb, source_id=source_id, title=title, local=False)
    assert landing.branch is not None
    (root / "drafts").mkdir(exist_ok=True)
    (root / "drafts" / "d.md").write_text("written\n")

    _git(root, "branch", "other-process-branch")
    real_commit = git.stage_and_commit

    def commit_then_drift(r, paths, message):
        sha = real_commit(r, paths, message)
        _git(root, "switch", "-q", "other-process-branch")  # the other process
        return sha

    monkeypatch.setattr("wakil.app.git_service.git.stage_and_commit", commit_then_drift)
    monkeypatch.setattr("wakil.app.git_service.gh_available", lambda: True)
    return source_id, landing


def test_drift_at_the_push_step_does_not_yank_the_other_branch(git_kb, monkeypatch):
    """The pre-commit path refuses politely; the post-commit path used to
    detect the same condition and then `git switch` anyway, which is strictly
    worse than the bug being fixed."""
    root = git_kb.root_path
    source_id, landing = _drift_setup(git_kb, monkeypatch, title="Drift Push")

    with pytest.raises(BranchDriftError) as excinfo:
        land_ingestion(
            git_kb,
            landing,
            source_id=source_id,
            files=["drafts/d.md"],
            title="Drift Push",
            summary=None,
            ingest_run_id=None,
            kind="source",
            phase="capture",
        )

    # The other process still owns the tree.
    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD") == "other-process-branch"
    message = str(excinfo.value)
    # And the message reports what is actually durable, not a fixed sentence.
    assert "nothing was committed" not in message
    assert "commit itself succeeded" in message
    assert landing.branch is not None
    # The commit really is durable on the ingest branch, as the message says.
    assert _git(root, "rev-parse", landing.branch) == _git(
        root, "log", "-1", "--format=%H", landing.branch
    )


def test_an_ordinary_push_failure_still_returns_to_default(git_kb, monkeypatch):
    """Only *drift* skips the return; a plain gh/push failure must not."""
    root = git_kb.root_path
    _add_local_origin(root)
    source_id = _insert_source(git_kb, "Push Fail")
    landing = prepare_landing(git_kb, source_id=source_id, title="Push Fail", local=False)
    (root / "drafts").mkdir(exist_ok=True)
    (root / "drafts" / "p.md").write_text("x\n")

    monkeypatch.setattr("wakil.app.git_service.gh_available", lambda: True)
    monkeypatch.setattr(
        "wakil.app.git_service.git.push_branch",
        lambda r, name: (_ for _ in ()).throw(git.GitError("push rejected")),
    )

    with pytest.raises(GitServiceError, match="push rejected"):
        land_ingestion(
            git_kb,
            landing,
            source_id=source_id,
            files=["drafts/p.md"],
            title="Push Fail",
            summary=None,
            ingest_run_id=None,
            kind="source",
            phase="capture",
        )
    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_a_follow_up_branch_forgets_the_merged_prs_url(git_kb, monkeypatch):
    """The recorded PR belongs to the branch being abandoned. Keeping it
    flipped a merged PR to ready and left the follow-up branch with no PR."""
    root = git_kb.root_path
    _add_local_origin(root)
    source_id = _insert_source(git_kb, "Merged Already")
    with open_session(git_kb) as session:
        row = session.get(Source, source_id)
        assert row is not None
        row.git_branch = "wakil/ingest/2026-01-01-merged-already"  # deleted after merge
        row.git_pr_url = "https://github.com/o/r/pull/1"
        session.commit()

    landing = prepare_landing(git_kb, source_id=source_id, title="Merged Already", local=False)

    assert landing.branch is not None
    assert "revision" in landing.branch
    with open_session(git_kb) as session:
        row = session.get(Source, source_id)
        assert row is not None
        assert row.git_pr_url is None  # so _land_pr opens one for the new branch
        assert row.git_branch == landing.branch


# --- round-2 review: gate the write, verify the commit, record it early ----


def test_assert_landing_intact_refuses_before_anything_is_written(git_kb):
    """The commit-time assertion fired after `apply_enrichment` had already
    rewritten the user's existing notes into another process's tree."""
    root = git_kb.root_path
    source_id = _insert_source(git_kb, "Pre-Write Gate")
    landing = prepare_landing(git_kb, source_id=source_id, title="Pre-Write Gate", local=False)
    _git(root, "switch", "-q", "main")  # the other process

    with pytest.raises(BranchDriftError) as excinfo:
        assert_landing_intact(git_kb, landing)
    assert "Nothing has been written yet." in str(excinfo.value)


def test_assert_landing_intact_is_a_no_op_for_local(git_kb):
    landing = prepare_landing(git_kb, source_id=None, title="Local", local=True)
    assert_landing_intact(git_kb, landing)  # must not raise


def test_drift_during_the_commit_is_caught_after_the_fact(git_kb, monkeypatch):
    """The pre-commit assertion only narrows the window. If HEAD moves between
    it and the commit, the commit lands elsewhere and the reported branch is a
    label again -- verbatim the #181 report through a smaller window."""
    root = git_kb.root_path
    source_id = _insert_source(git_kb, "Race The Commit")
    landing = prepare_landing(git_kb, source_id=source_id, title="Race The Commit", local=False)
    assert landing.branch is not None
    (root / "drafts").mkdir(exist_ok=True)
    (root / "drafts" / "r.md").write_text("x\n")

    real_commit = git.stage_and_commit

    def drift_then_commit(r, paths, message):
        # Slip in between the assertion and the commit.
        _git(root, "switch", "-q", "main")
        return real_commit(r, paths, message)

    monkeypatch.setattr("wakil.app.git_service.git.stage_and_commit", drift_then_commit)

    with pytest.raises(BranchDriftError, match="did not land on"):
        land_ingestion(
            git_kb,
            landing,
            source_id=source_id,
            files=["drafts/r.md"],
            title="Race The Commit",
            summary=None,
            ingest_run_id=None,
            kind="source",
            phase="capture",
        )


def test_a_commit_is_recorded_even_when_the_pr_step_fails(git_kb, monkeypatch):
    """A durable commit with no `git_changes` row was invisible to wakil, and
    re-running could not recover: `stage_and_commit` fails outright once there
    is nothing left to commit."""
    root = git_kb.root_path
    _add_local_origin(root)
    source_id = _insert_source(git_kb, "PR Fails")
    landing = prepare_landing(git_kb, source_id=source_id, title="PR Fails", local=False)
    (root / "drafts").mkdir(exist_ok=True)
    (root / "drafts" / "q.md").write_text("x\n")

    monkeypatch.setattr("wakil.app.git_service.gh_available", lambda: True)
    monkeypatch.setattr(
        "wakil.app.git_service.git.push_branch",
        lambda r, name: (_ for _ in ()).throw(git.GitError("push rejected")),
    )

    with pytest.raises(GitServiceError, match="push rejected"):
        land_ingestion(
            git_kb,
            landing,
            source_id=source_id,
            files=["drafts/q.md"],
            title="PR Fails",
            summary=None,
            ingest_run_id=None,
            kind="source",
            phase="capture",
        )

    with open_session(git_kb) as session:
        change = session.scalar(select(GitChange))
        assert change is not None, "the durable commit must be visible to wakil"
        assert change.branch_name == landing.branch
        assert change.pr_url is None  # honest: there is no PR


def test_the_pr_url_is_attached_to_the_existing_change_row(git_kb, monkeypatch):
    """Recording early must not produce two rows for one commit."""
    root = git_kb.root_path
    _add_local_origin(root)
    source_id = _insert_source(git_kb, "PR OK")
    landing = prepare_landing(git_kb, source_id=source_id, title="PR OK", local=False)
    (root / "drafts").mkdir(exist_ok=True)
    (root / "drafts" / "o.md").write_text("x\n")

    monkeypatch.setattr("wakil.app.git_service.gh_available", lambda: True)
    monkeypatch.setattr("wakil.app.git_service.git.push_branch", lambda r, name: None)
    monkeypatch.setattr("wakil.app.git_service.find_pull_request", lambda r, head: None)
    monkeypatch.setattr(
        "wakil.app.git_service.create_pull_request",
        lambda r, title, body, *, head, base, draft=False: "https://pr.url/7",
    )

    outcome = land_ingestion(
        git_kb,
        landing,
        source_id=source_id,
        files=["drafts/o.md"],
        title="PR OK",
        summary=None,
        ingest_run_id=None,
        kind="source",
        phase="capture",
    )
    assert outcome.pr_url == "https://pr.url/7"
    with open_session(git_kb) as session:
        changes = list(session.scalars(select(GitChange)))
        assert len(changes) == 1
        assert changes[0].pr_url == "https://pr.url/7"


def test_abandon_does_not_yank_the_other_branch(git_kb):
    """`abandon_landing` reaches its call sites after minutes of model calls
    (and, over MCP, across an unbounded prepare/apply gap). Returning to the
    default branch then is the same clobber `land_ingestion` refuses — for a
    landing that wrote nothing at all."""
    from wakil.app.git_service import abandon_landing

    root = git_kb.root_path
    landing = prepare_landing(git_kb, source_id=None, title="Nothing", local=False)
    assert landing.branch is not None

    # Another process takes the working tree while this one was thinking.
    _git(root, "switch", "-q", "-c", "other-process-branch")

    abandon_landing(git_kb, landing)

    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD") == "other-process-branch"


def test_abandon_still_returns_when_the_tree_is_ours(git_kb):
    """The ordinary case is unchanged: no drift, so the session goes home
    rather than being stranded on a throwaway ingest branch."""
    from wakil.app.git_service import abandon_landing

    root = git_kb.root_path
    default = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    landing = prepare_landing(git_kb, source_id=None, title="Nothing", local=False)
    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD") == landing.branch

    abandon_landing(git_kb, landing)

    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD") == default


def test_drift_during_the_pr_step_does_not_yank_on_the_success_path(git_kb, monkeypatch):
    """`_land_pr` asserts HEAD before the push, but the push plus a 120s `gh`
    timeout run after that. A process taking the tree in that window used to
    be switched to the default branch, and the landing reported success."""
    root = git_kb.root_path
    _add_local_origin(root)  # without a remote, `_land_pr` returns before the window
    source_id = _insert_source(git_kb, title="Late Drift")
    landing = prepare_landing(git_kb, source_id=source_id, title="Late Drift", local=False)
    assert landing.branch is not None
    (root / "drafts").mkdir(exist_ok=True)
    (root / "drafts" / "d.md").write_text("hi\n", encoding="utf-8")

    def _steal_the_tree(*args, **kwargs):
        _git(root, "switch", "-q", "-c", "other-process-branch")
        return "https://pr.url/1"

    monkeypatch.setattr("wakil.app.git_service.gh_available", lambda: True)
    monkeypatch.setattr("wakil.app.git_service.find_pull_request", lambda *a, **k: None)
    monkeypatch.setattr("wakil.app.git_service.git.push_branch", lambda r, name: None)
    monkeypatch.setattr("wakil.app.git_service.create_pull_request", _steal_the_tree)

    outcome = land_ingestion(
        git_kb,
        landing,
        source_id=source_id,
        files=["drafts/d.md"],
        title="Late Drift",
        summary=None,
        ingest_run_id=None,
        kind="source",
        phase="capture",
    )

    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD") == "other-process-branch"
    # And the outcome reports where the tree actually is, not where we meant
    # to leave it.
    assert outcome.returned_to == "other-process-branch"


def test_drift_during_the_commit_records_where_the_commit_actually_went(git_kb, monkeypatch):
    """The commit is durable but on the wrong branch. Leaving `git_branch`
    naming a branch that provably lacks it sends the next `wakil enrich` to
    check out a branch with no raw capture on it — "Could not read raw
    capture", which is the #180 symptom the early recording exists to
    prevent."""
    root = git_kb.root_path
    source_id = _insert_source(git_kb, "Race The Record")
    landing = prepare_landing(git_kb, source_id=source_id, title="Race The Record", local=False)
    assert landing.branch is not None
    (root / "drafts").mkdir(exist_ok=True)
    (root / "drafts" / "rr.md").write_text("x\n")

    real_commit = git.stage_and_commit

    def drift_then_commit(r, paths, message):
        _git(root, "switch", "-q", "main")
        return real_commit(r, paths, message)

    monkeypatch.setattr("wakil.app.git_service.git.stage_and_commit", drift_then_commit)

    with pytest.raises(BranchDriftError):
        land_ingestion(
            git_kb,
            landing,
            source_id=source_id,
            files=["drafts/rr.md"],
            title="Race The Record",
            summary=None,
            ingest_run_id=None,
            kind="source",
            phase="capture",
        )

    sha = _git(root, "rev-parse", "main")
    with open_session(git_kb) as session:
        source = session.get(Source, source_id)
        assert source is not None
        # Cleared, not repointed at `main`. `git_branch` is what the next run
        # resumes onto and then commits and pushes to, so recording the
        # default branch there would turn the next `wakil enrich` into a
        # direct push of rewritten notes to `origin/main` with no PR — worse
        # than the stale pointer this path exists to fix. Clearing is safe
        # here precisely because the commit is on the default branch, so the
        # fresh branch the next run cuts from it already has the capture.
        assert source.git_branch is None
        changes = session.scalars(select(GitChange)).all()
        # The durable commit is recorded, and against the right branch.
        assert [(c.commit_sha, c.branch_name) for c in changes] == [(sha, "main")]


def test_a_drifted_commit_on_a_wakil_branch_is_still_recorded(git_kb, monkeypatch):
    """Clearing is for the default branch. If the commit landed on another
    *wakil ingest* branch, that is a legitimate resume target and keeping it
    is what stops the next run from cutting a branch without the capture."""
    root = git_kb.root_path
    source_id = _insert_source(git_kb, "Race A Wakil Branch")
    landing = prepare_landing(
        git_kb, source_id=source_id, title="Race A Wakil Branch", local=False
    )
    assert landing.branch is not None
    (root / "drafts").mkdir(exist_ok=True)
    (root / "drafts" / "rw.md").write_text("x\n")

    other = "wakil/ingest/2026-08-09-other-process"
    real_commit = git.stage_and_commit

    def drift_then_commit(r, paths, message):
        _git(root, "switch", "-q", "-c", other)
        return real_commit(r, paths, message)

    monkeypatch.setattr("wakil.app.git_service.git.stage_and_commit", drift_then_commit)

    with pytest.raises(BranchDriftError):
        land_ingestion(
            git_kb,
            landing,
            source_id=source_id,
            files=["drafts/rw.md"],
            title="Race A Wakil Branch",
            summary=None,
            ingest_run_id=None,
            kind="source",
            phase="capture",
        )

    with open_session(git_kb) as session:
        source = session.get(Source, source_id)
        assert source is not None
        assert source.git_branch == other
