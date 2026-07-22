import subprocess
from pathlib import Path

from sqlalchemy import select

from wakil.app.workspace_service import get_status, init_workspace, open_session
from wakil.config.settings import WorkspaceConfig, is_initialized
from wakil.storage.schema import Note, User, Workspace


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_init_creates_config_and_database(kb_path: Path):
    status, result = init_workspace(kb_path)

    assert is_initialized(kb_path)
    assert (kb_path / ".wakil" / "wakil.db").is_file()
    assert status.config.name == "kb"
    assert result.added == 8
    assert status.note_count == 8


def test_init_creates_default_user_and_workspace(kb_path: Path):
    init_workspace(kb_path, name="my-kb")

    config = WorkspaceConfig.load(kb_path)
    assert config.name == "my-kb"
    with open_session(config) as session:
        assert session.scalar(select(User)) is not None
        workspace = session.scalar(select(Workspace))
        assert workspace.name == "my-kb"
        assert workspace.root_path == str(kb_path.resolve())


def test_reindex_is_idempotent(kb_path: Path):
    init_workspace(kb_path)
    _, second = init_workspace(kb_path)

    assert second.added == 0
    assert second.updated == 0
    assert second.unchanged == 8


def test_reindex_detects_changes_and_removals(kb_path: Path):
    init_workspace(kb_path)

    (kb_path / "drafts" / "rough-idea.md").write_text("# A Rough Idea\n\nEdited.\n")
    (kb_path / "people" / "jane-doe.md").unlink()
    (kb_path / "concepts" / "new-idea.md").write_text("# New Idea\n")

    _, result = init_workspace(kb_path)
    assert result.updated == 1
    assert result.removed == 1
    assert result.added == 1

    config = WorkspaceConfig.load(kb_path)
    with open_session(config) as session:
        paths = set(session.scalars(select(Note.path)))
    assert "people/jane-doe.md" not in paths
    assert "concepts/new-idea.md" in paths


def test_status_reports_special_files(kb_path: Path):
    init_workspace(kb_path)
    status = get_status(kb_path)

    assert "README.md" in status.special_files
    assert "RESOLVER.md" not in status.special_files
    assert "AGENTS.md" not in status.special_files


def test_status_without_git_repo(kb_path: Path):
    init_workspace(kb_path)
    status = get_status(kb_path)
    assert status.git.is_repo is False


def _git_kb_with_worktree(kb_path: Path, worktree_path: Path) -> None:
    _git(kb_path, "init", "-q", "-b", "main")
    _git(kb_path, "config", "user.email", "test@example.com")
    _git(kb_path, "config", "user.name", "Test User")
    _git(kb_path, "config", "commit.gpgsign", "false")
    _git(kb_path, "add", "-A")
    _git(kb_path, "commit", "-q", "-m", "seed")
    init_workspace(kb_path)
    _git(kb_path, "worktree", "add", "-q", "-b", "scratch/worker", str(worktree_path), "main")


def test_linked_worktree_shares_workspace_with_main(tmp_path: Path, kb_path: Path):
    worktree = tmp_path / "worktree"
    _git_kb_with_worktree(kb_path, worktree)

    main_config = WorkspaceConfig.load(kb_path)
    assert main_config.is_linked_worktree is False
    assert main_config.state_root == kb_path.resolve()

    init_workspace(worktree)
    worktree_config = WorkspaceConfig.load(worktree)
    assert worktree_config.is_linked_worktree is True
    assert worktree_config.root_path == worktree.resolve()
    assert worktree_config.state_root == kb_path.resolve()

    # .wakil/ was never created inside the linked worktree -- both configs
    # resolve to the one at the main worktree's root.
    assert not (worktree / ".wakil").exists()
    assert worktree_config.database_path == main_config.database_path

    # One Workspace row, not two -- both configs see the same id.
    with open_session(main_config) as session:
        workspaces = list(session.scalars(select(Workspace)))
    assert len(workspaces) == 1
    assert workspaces[0].root_path == str(kb_path.resolve())


def test_is_initialized_recognizes_linked_worktree(tmp_path: Path, kb_path: Path):
    worktree = tmp_path / "worktree"
    _git_kb_with_worktree(kb_path, worktree)

    # Never ran `wakil init` *in* the worktree -- it should still be seen
    # as initialized, via the main worktree's .wakil/.
    assert is_initialized(worktree)


def test_linked_worktree_indexing_does_not_prune_main_notes(tmp_path: Path, kb_path: Path):
    worktree = tmp_path / "worktree"
    _git_kb_with_worktree(kb_path, worktree)

    # The worktree's branch predates a file that only exists on main.
    (kb_path / "new-from-main.md").write_text("# Only on main\n")
    _git(kb_path, "add", "-A")
    _git(kb_path, "commit", "-q", "-m", "add a main-only file")
    init_workspace(kb_path)  # re-index main so the new note is recorded

    # The worktree's own checkout (still at the pre-existing commit) has no
    # such file -- indexing from there must not delete the note.
    assert not (worktree / "new-from-main.md").exists()
    init_workspace(worktree)

    config = WorkspaceConfig.load(kb_path)
    with open_session(config) as session:
        paths = set(session.scalars(select(Note.path)))
    assert "new-from-main.md" in paths
