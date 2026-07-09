from pathlib import Path

from sqlalchemy import select

from wakil.app.workspace_service import get_status, init_workspace, open_session
from wakil.config.settings import WorkspaceConfig, is_initialized
from wakil.storage.schema import Note, User, Workspace


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
    assert "SCHEMA.md" in status.special_files
    assert "AGENTS.md" not in status.special_files


def test_status_without_git_repo(kb_path: Path):
    init_workspace(kb_path)
    status = get_status(kb_path)
    assert status.git.is_repo is False
