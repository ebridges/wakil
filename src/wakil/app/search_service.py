"""Hybrid search: QMD over the Markdown knowledge base + SQLite FTS."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from wakil.app.memory_service import retrieval_rank
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
    state: str | None = None  # memory lifecycle state, for memory hits


def get_workspace_id(session: Session, config: WorkspaceConfig) -> int | None:
    return session.scalar(select(Workspace.id).where(Workspace.root_path == str(config.state_root)))


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
        for result in qmd_search(config.root_path, config.qmd_dir, query, limit=limit, mode=mode):
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
    hits.extend(_ranked_memory_hits(fts.search_memories(session, workspace_id, query, limit=limit)))
    for row in fts.search_sources(session, workspace_id, query, limit=limit):
        hits.append(_fts_hit("source", row))

    return hits


def _ranked_memory_hits(rows: list[dict]) -> list[SearchHit]:
    """Order memory hits by lifecycle rank (durable first, faded/archived last),
    breaking ties by confidence within the same state, then by bm25 relevance."""

    def sort_key(row: dict) -> tuple[float, float]:
        return (
            retrieval_rank(row["state"], _parse_dt(row["created_at"]), row.get("confidence")),
            row["score"],
        )

    return [_fts_hit("memory", row) for row in sorted(rows, key=sort_key)]


def _parse_dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _fts_hit(kind: str, row: dict) -> SearchHit:
    return SearchHit(
        kind=kind,
        ref=row["ref"],
        title=row["title"] or row["ref"],
        snippet=row["snippet"] or "",
        engine="fts",
        score=row["score"],
        state=row.get("state"),
    )
