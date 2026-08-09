"""Workspace initialization, note indexing, and status inspection."""

import json
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from wakil.config.settings import (
    QMD_DIRNAME,
    SPECIAL_FILES,
    WAKIL_DIR,
    WorkspaceConfig,
    is_initialized,
    resolve_state_root,
)
from wakil.integrations.git import GitInfo, inspect_git
from wakil.integrations.qmd import QmdInfo, detect_qmd
from wakil.knowledge.markdown import MarkdownFile, discover_markdown_files, read_markdown_file
from wakil.knowledge.wikilinks import normalize_target, parse_wikilinks
from wakil.storage.database import create_db_engine, init_db, make_session_factory
from wakil.storage.schema import Memory, Note, Relationship, Source, User, Workspace, utcnow

# Predicate applied to every generic Note↔Note edge extracted at index
# time (ADR 0006, docs/relationship-graph-traversal-proposal.md Phase 1).
# `Relationship.predicate` is free-string by convention (see ADR 0013),
# so this is a documented value, not an enforced enum.
MENTIONS_PREDICATE = "mentions"

DEFAULT_USER_NAME = "default"


@dataclass
class SourceRelink:
    """A `sources.raw_text_path` that indexing repointed after a rename."""

    source_id: int
    old_path: str
    new_path: str


@dataclass
class IndexResult:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    # Reported rather than applied silently: this is wakil changing a pointer
    # into the user's own knowledge base on its own initiative, and a wrong
    # guess sends `enrich` at the wrong file (working agreement item 12).
    sources_relinked: list[SourceRelink] = field(default_factory=list)

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
            state_root=resolve_state_root(root),
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
        # A linked worktree only ever has a subset of the shared workspace's
        # branches checked out at once -- notes committed on another
        # worktree's branch are real, just not on disk *here* right now.
        # Only the canonical checkout prunes notes that are genuinely gone;
        # a linked worktree only adds/updates what it can see.
        index_result = index_notes(session, workspace.id, root, prune=not config.is_linked_worktree)
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
        select(Workspace).where(Workspace.root_path == str(config.state_root))
    )
    if workspace is None:
        workspace = Workspace(
            user_id=user.id,
            name=config.name,
            root_path=str(config.state_root),
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


def _follow_renamed_raw_captures(
    session: Session,
    workspace_id: int,
    existing: dict[str, Note],
    parsed: dict[str, "MarkdownFile"],
    seen: set[str],
    result: IndexResult,
) -> None:
    """Repoint `sources.raw_text_path` when a raw capture is moved on disk.

    `index` already noticed the rename for the `notes` table, but nothing
    propagated it to `sources`, so a `git mv`'d capture left `enrich` failing
    with "Could not read raw capture <old path>" and no supported way to fix
    it (#178). A file that disappeared from one path and appeared at another
    with an identical content hash is a move.

    Content-identical only, deliberately: if the file was also *edited* while
    being moved there is no reliable way to tell a rename from an unrelated
    new note, and guessing would silently repoint a source at the wrong file.
    `wakil sources relink` covers that case explicitly.
    """
    gone = {
        note.content_hash: path
        for path, note in existing.items()
        if path not in seen and note.content_hash
    }
    if not gone:
        return
    known = set(existing)
    moves = {
        gone[md.content_hash]: path
        for path, md in parsed.items()
        if path not in known and md.content_hash in gone
    }
    if not moves:
        return
    for source in session.scalars(
        select(Source).where(
            Source.workspace_id == workspace_id, Source.raw_text_path.in_(list(moves))
        )
    ):
        if source.raw_text_path is not None:
            old_path = source.raw_text_path
            new_path = moves[old_path]
            source.raw_text_path = new_path
            result.sources_relinked.append(
                SourceRelink(source_id=source.id, old_path=old_path, new_path=new_path)
            )


def index_notes(
    session: Session, workspace_id: int, root: Path, *, prune: bool = True
) -> IndexResult:
    """Sync the notes table with the Markdown files currently on disk.

    Also extracts each note body's `[[wikilinks]]` into generic `mentions`
    Relationship rows (ADR 0006 Phase 1) — this is the only place backlinks
    ever become real for hand-edited or wakil-authored content.

    `prune=False` skips removing notes missing from `root` — for a linked
    git worktree, "missing here" just means "on a different branch," not
    gone; only the canonical checkout (see `init_workspace`) should be
    treated as authoritative for deletions.
    """
    result = IndexResult()
    existing = {
        note.path: note
        for note in session.scalars(select(Note).where(Note.workspace_id == workspace_id))
    }
    seen: set[str] = set()
    # Kept per-key so mentions-sync doesn't have to re-read files from disk.
    parsed: dict[str, MarkdownFile] = {}

    for relative_path in discover_markdown_files(root):
        md = read_markdown_file(root, relative_path)
        key = str(relative_path)
        seen.add(key)
        parsed[key] = md
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

    if prune:
        _follow_renamed_raw_captures(session, workspace_id, existing, parsed, seen, result)
        removed_note_ids: list[int] = []
        for key, note in existing.items():
            if key not in seen:
                removed_note_ids.append(note.id)
                session.delete(note)
                result.removed += 1
        if removed_note_ids:
            # SQLite has no ON DELETE CASCADE wired up (see
            # database.py — FK enforcement isn't on), so orphan Note↔Note
            # rows have to be cleaned up explicitly here rather than
            # relying on the DB to cascade.
            session.execute(
                delete(Relationship).where(
                    Relationship.workspace_id == workspace_id,
                    or_(
                        Relationship.subject_note_id.in_(removed_note_ids),
                        Relationship.object_note_id.in_(removed_note_ids),
                    ),
                )
            )

    # Note-row inserts above are pending until flush; the mentions-sync
    # step below needs their ids to build edges. Everything after this
    # commits in the caller's own outer transaction — no separate commit.
    session.flush()
    _sync_note_mentions(session, workspace_id, seen, parsed)

    return result


def _sync_note_mentions(
    session: Session,
    workspace_id: int,
    present_paths: set[str],
    parsed: dict[str, MarkdownFile],
) -> None:
    """Reconcile `mentions` Note↔Note edges against wikilinks on disk.

    For every note we saw on disk this run, compute the set of wikilink
    targets that resolve to real notes and diff against the existing
    `mentions` rows for that note — insert what's new, delete what's gone.
    Dead-link targets (wikilinks pointing at nothing) are skipped
    silently: dead-link detection is `maintain`'s job, not indexing's.

    Notes not in `present_paths` (linked-worktree case: file exists on
    another branch, not here) are left completely alone — outgoing edges
    for those notes are preserved rather than mistakenly pruned.
    """
    notes = list(session.scalars(select(Note).where(Note.workspace_id == workspace_id)))
    # Resolve wikilink targets against the same normalized path form —
    # this kb mixes [[people/x]] and [[sources/y.md]] for the identical
    # note (see ingest_service._normalize_link_path's own comment).
    by_norm_path: dict[str, Note] = {}
    for note in notes:
        by_norm_path[normalize_target(note.path)] = note

    subject_ids = [
        note.id for note in notes if note.path in present_paths and note.path in parsed
    ]
    if not subject_ids:
        return

    existing_edges: dict[int, dict[int, Relationship]] = {}
    for edge in session.scalars(
        select(Relationship).where(
            Relationship.workspace_id == workspace_id,
            Relationship.predicate == MENTIONS_PREDICATE,
            Relationship.subject_note_id.in_(subject_ids),
            Relationship.object_note_id.is_not(None),
        )
    ):
        # subject_note_id is filtered by .in_(subject_ids) and object_note_id
        # by is_not(None) in the query above -- neither is ever null here.
        assert edge.subject_note_id is not None
        assert edge.object_note_id is not None
        existing_edges.setdefault(edge.subject_note_id, {})[edge.object_note_id] = edge

    for note in notes:
        if note.path not in present_paths:
            continue
        md = parsed.get(note.path)
        if md is None:
            continue
        wanted: set[int] = set()
        for link in parse_wikilinks(md.body):
            target = by_norm_path.get(normalize_target(link.target))
            if target is None or target.id == note.id:
                # Unresolved (dead link) or self-link — skip. A self-link
                # would produce a degenerate A→A row that no query cares
                # about; keep the table clean.
                continue
            wanted.add(target.id)

        current = existing_edges.get(note.id, {})
        for object_id in wanted - current.keys():
            session.add(
                Relationship(
                    workspace_id=workspace_id,
                    subject_note_id=note.id,
                    object_note_id=object_id,
                    predicate=MENTIONS_PREDICATE,
                )
            )
        for object_id in current.keys() - wanted:
            session.delete(current[object_id])


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
