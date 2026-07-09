"""Hybrid search: QMD over the Markdown knowledge base + SQLite FTS."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from wakil.config.settings import WorkspaceConfig
from wakil.integrations.qmd import qmd_search
from wakil.storage import fts
from wakil.storage.schema import Workspace


@dataclass
class SearchHit:
    kind: str  # note | memory | source
    ref: str  # note path, "memory:<id>", or "source:<id>"
    title: str
    snippet: str
    engine: str  # qmd | fts
    score: float | None = None


def get_workspace_id(session: Session, config: WorkspaceConfig) -> int | None:
    return session.scalar(select(Workspace.id).where(Workspace.root_path == str(config.root_path)))


def search_workspace(
    session: Session,
    config: WorkspaceConfig,
    query: str,
    limit: int = 10,
    mode: str = "search",
) -> list[SearchHit]:
    """QMD results first (knowledge base is the source of truth), then FTS."""
    hits: list[SearchHit] = []
    seen_notes: set[str] = set()

    if config.qmd_enabled:
        for result in qmd_search(config.root_path, query, limit=limit, mode=mode):
            seen_notes.add(result.path)
            hits.append(
                SearchHit(
                    kind="note",
                    ref=result.path,
                    title=result.title or result.path,
                    snippet=result.snippet,
                    engine="qmd",
                    score=result.score,
                )
            )

    workspace_id = get_workspace_id(session, config)
    if workspace_id is None:
        return hits

    for row in fts.search_notes(session, workspace_id, query, limit=limit):
        if row["ref"] in seen_notes:
            continue
        hits.append(_fts_hit("note", row))
    for row in fts.search_memories(session, workspace_id, query, limit=limit):
        hits.append(_fts_hit("memory", row))
    for row in fts.search_sources(session, workspace_id, query, limit=limit):
        hits.append(_fts_hit("source", row))

    return hits


def _fts_hit(kind: str, row: dict) -> SearchHit:
    return SearchHit(
        kind=kind,
        ref=row["ref"],
        title=row["title"] or row["ref"],
        snippet=row["snippet"] or "",
        engine="fts",
        score=row["score"],
    )
