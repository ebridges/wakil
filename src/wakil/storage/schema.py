"""SQLite schema for wakil's operational store.

The data model is single-user today but keeps user_id columns so the same
schema can serve multiple users later. Markdown stays the source of truth;
these tables index, cache, and record operational history.
"""

from datetime import UTC, date, datetime

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(200))
    root_path: Mapped[str] = mapped_column(Text)
    git_remote: Mapped[str | None] = mapped_column(Text, default=None)
    qmd_enabled: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("workspace_id", "content_hash", name="uq_sources_workspace_content_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    source_type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str | None] = mapped_column(Text, default=None)
    origin: Mapped[str | None] = mapped_column(Text, default=None)
    author: Mapped[str | None] = mapped_column(Text, default=None)
    published_at: Mapped[datetime | None] = mapped_column(default=None)
    retrieved_at: Mapped[datetime | None] = mapped_column(default=None)
    content_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    raw_text_path: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(String(30), default="new")
    metadata_json: Mapped[str | None] = mapped_column(Text, default=None)
    git_branch: Mapped[str | None] = mapped_column(Text, default=None)
    git_pr_url: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Note(Base):
    __tablename__ = "notes"
    __table_args__ = (UniqueConstraint("workspace_id", "path", name="uq_notes_workspace_path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    path: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text, default=None)
    frontmatter_json: Mapped[str | None] = mapped_column(Text, default=None)
    content_hash: Mapped[str] = mapped_column(String(64))
    last_indexed_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    memory_type: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text, default=None)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), default=None)
    note_id: Mapped[int | None] = mapped_column(ForeignKey("notes.id"), default=None)
    confidence: Mapped[float | None] = mapped_column(default=None)
    # Commitment/register axis, orthogonal to memory_type (docs/adr/0014):
    # "casual" marks a low-commitment claim (e.g. a 1:1 hot take); None/
    # "formal" is the default. Distinct from confidence, which is about
    # certainty rather than the register a claim was uttered in. Named
    # `stance` rather than `register` because the latter collides with
    # ABCMeta.register on Pydantic's ModelMetaclass (see
    # CandidateMemoryModel.stance) -- kept consistent here rather than
    # translating names at the ORM boundary.
    stance: Mapped[str | None] = mapped_column(String(10), default=None)
    state: Mapped[str] = mapped_column(String(20), default="working")
    importance: Mapped[float | None] = mapped_column(default=None)
    freshness: Mapped[float | None] = mapped_column(default=None)
    # When the described event happened (Timeline ordering) — distinct from
    # created_at, which records when this row was written (entity-model.md).
    event_date: Mapped[date | None] = mapped_column(default=None)
    last_seen_at: Mapped[datetime | None] = mapped_column(default=None)
    metadata_json: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Relationship(Base):
    __tablename__ = "relationships"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    subject_memory_id: Mapped[int] = mapped_column(ForeignKey("memories.id"))
    predicate: Mapped[str] = mapped_column(String(50))
    object_memory_id: Mapped[int] = mapped_column(ForeignKey("memories.id"))
    # Note↔Note structural edges (wikilinks/backlinks) are a different thing
    # from Memory↔Memory semantic edges; these columns widen the table to
    # carry both, mirroring the nullable-provenance pattern of source_id/
    # note_id (entity-model.md). Backlinks become a live query
    # (WHERE object_note_id = X), never stored prose.
    subject_note_id: Mapped[int | None] = mapped_column(ForeignKey("notes.id"), default=None)
    object_note_id: Mapped[int | None] = mapped_column(ForeignKey("notes.id"), default=None)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), default=None)
    note_id: Mapped[int | None] = mapped_column(ForeignKey("notes.id"), default=None)
    confidence: Mapped[float | None] = mapped_column(default=None)
    metadata_json: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class IngestRun(Base):
    __tablename__ = "ingest_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), default=None)
    status: Mapped[str] = mapped_column(String(30), default="started")
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(default=None)
    created_branch: Mapped[str | None] = mapped_column(Text, default=None)
    created_commit: Mapped[str | None] = mapped_column(Text, default=None)
    created_pr_url: Mapped[str | None] = mapped_column(Text, default=None)
    summary: Mapped[str | None] = mapped_column(Text, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    metadata_json: Mapped[str | None] = mapped_column(Text, default=None)


class QueryRun(Base):
    __tablename__ = "query_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    query: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="started")
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(default=None)
    sources_used_json: Mapped[str | None] = mapped_column(Text, default=None)
    notes_used_json: Mapped[str | None] = mapped_column(Text, default=None)
    memories_used_json: Mapped[str | None] = mapped_column(Text, default=None)
    answer: Mapped[str | None] = mapped_column(Text, default=None)
    metadata_json: Mapped[str | None] = mapped_column(Text, default=None)


class GitChange(Base):
    __tablename__ = "git_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    operation: Mapped[str] = mapped_column(String(50))
    branch_name: Mapped[str | None] = mapped_column(Text, default=None)
    commit_sha: Mapped[str | None] = mapped_column(String(64), default=None)
    pr_url: Mapped[str | None] = mapped_column(Text, default=None)
    summary: Mapped[str | None] = mapped_column(Text, default=None)
    metadata_json: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
