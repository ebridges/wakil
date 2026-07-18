"""Workspace initialization, note indexing, and status inspection."""

import json
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wakil.config.settings import (
    QMD_DIRNAME,
    SPECIAL_FILES,
    WAKIL_DIR,
    WorkspaceConfig,
    is_initialized,
)
from wakil.integrations.git import GitInfo, inspect_git
from wakil.integrations.qmd import QmdInfo, detect_qmd
from wakil.knowledge.markdown import discover_markdown_files, read_markdown_file
from wakil.storage.database import create_db_engine, init_db, make_session_factory
from wakil.storage.schema import Memory, Note, Source, User, Workspace, utcnow

DEFAULT_USER_NAME = "default"


@dataclass
class IndexResult:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0

    @property
    def total(self) -> int:
        return self.added + self.updated + self.unchanged


@dataclass
class WorkspaceStatus:
    config: WorkspaceConfig
    note_count: int
    source_count: int
    memory_count: int
    git: GitInfo
    qmd: QmdInfo
    special_files: list[str] = field(default_factory=list)


def open_session(config: WorkspaceConfig) -> Session:
    engine = create_db_engine(config.database_path)
    init_db(engine)
    return make_session_factory(engine)()


def init_workspace(root: Path, name: str | None = None) -> tuple[WorkspaceStatus, IndexResult]:
    """Initialize (or re-open) a workspace and index its Markdown files."""
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)

    git_info = inspect_git(root)
    qmd_info = detect_qmd(root, qmd_dir=root / WAKIL_DIR / QMD_DIRNAME)

    if is_initialized(root):
        config = WorkspaceConfig.load(root)
    else:
        config = WorkspaceConfig(
            name=name or root.name,
            root_path=root,
            git_remote=git_info.remote_url,
            qmd_enabled=qmd_info.available,
        )
    if name:
        config.name = name
    config.git_remote = git_info.remote_url
    config.qmd_enabled = qmd_info.available
    config.save()

    with open_session(config) as session:
        workspace = _ensure_workspace(session, config)
        index_result = index_notes(session, workspace.id, root)
        session.commit()
        status = _build_status(session, config, workspace.id, git_info, qmd_info)
    return status, index_result


def get_status(root: Path) -> WorkspaceStatus:
    config = WorkspaceConfig.load(root)
    git_info = inspect_git(root)
    qmd_info = detect_qmd(root, qmd_dir=config.qmd_dir)
    with open_session(config) as session:
        workspace = _ensure_workspace(session, config)
        session.commit()
        return _build_status(session, config, workspace.id, git_info, qmd_info)


def _ensure_workspace(session: Session, config: WorkspaceConfig) -> Workspace:
    user = session.scalar(select(User).limit(1))
    if user is None:
        user = User(display_name=DEFAULT_USER_NAME)
        session.add(user)
        session.flush()

    workspace = session.scalar(
        select(Workspace).where(Workspace.root_path == str(config.root_path))
    )
    if workspace is None:
        workspace = Workspace(
            user_id=user.id,
            name=config.name,
            root_path=str(config.root_path),
            git_remote=config.git_remote,
            qmd_enabled=config.qmd_enabled,
        )
        session.add(workspace)
        session.flush()
    else:
        workspace.name = config.name
        workspace.git_remote = config.git_remote
        workspace.qmd_enabled = config.qmd_enabled
    return workspace


def index_notes(session: Session, workspace_id: int, root: Path) -> IndexResult:
    """Sync the notes table with the Markdown files currently on disk."""
    result = IndexResult()
    existing = {
        note.path: note
        for note in session.scalars(select(Note).where(Note.workspace_id == workspace_id))
    }
    seen: set[str] = set()

    for relative_path in discover_markdown_files(root):
        md = read_markdown_file(root, relative_path)
        key = str(relative_path)
        seen.add(key)
        note = existing.get(key)
        if note is None:
            session.add(
                Note(
                    workspace_id=workspace_id,
                    path=key,
                    title=md.title,
                    frontmatter_json=json.dumps(md.metadata, default=str) if md.metadata else None,
                    content_hash=md.content_hash,
                )
            )
            result.added += 1
        elif note.content_hash != md.content_hash:
            note.title = md.title
            note.frontmatter_json = json.dumps(md.metadata, default=str) if md.metadata else None
            note.content_hash = md.content_hash
            note.last_indexed_at = utcnow()
            result.updated += 1
        else:
            result.unchanged += 1

    for key, note in existing.items():
        if key not in seen:
            session.delete(note)
            result.removed += 1

    return result


def _build_status(
    session: Session,
    config: WorkspaceConfig,
    workspace_id: int,
    git_info: GitInfo,
    qmd_info: QmdInfo,
) -> WorkspaceStatus:
    def count(model) -> int:
        return (
            session.scalar(
                select(func.count()).select_from(model).where(model.workspace_id == workspace_id)
            )
            or 0
        )

    special = [name for name in SPECIAL_FILES if (config.root_path / name).is_file()]
    return WorkspaceStatus(
        config=config,
        note_count=count(Note),
        source_count=count(Source),
        memory_count=count(Memory),
        git=git_info,
        qmd=qmd_info,
        special_files=special,
    )
