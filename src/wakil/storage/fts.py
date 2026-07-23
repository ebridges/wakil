"""SQLite FTS5 indexes over notes, memories, and sources.

External-content FTS tables kept in sync with triggers, so ordinary ORM
writes (note indexing, memory/source inserts) update the search index
without extra application code.
"""

import re

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

# (fts table, content table, indexed columns)
_FTS_TABLES = [
    ("notes_fts", "notes", ["title", "path", "frontmatter_json"]),
    ("memories_fts", "memories", ["content", "summary"]),
    ("sources_fts", "sources", ["title", "origin", "author"]),
]


def _fts_statements() -> list[str]:
    statements = []
    for fts, table, columns in _FTS_TABLES:
        cols = ", ".join(columns)
        new_vals = ", ".join(f"new.{c}" for c in columns)
        old_vals = ", ".join(f"old.{c}" for c in columns)
        statements += [
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {fts} USING fts5("
            f"{cols}, content='{table}', content_rowid='id')",
            f"CREATE TRIGGER IF NOT EXISTS {table}_fts_insert AFTER INSERT ON {table} BEGIN "
            f"INSERT INTO {fts}(rowid, {cols}) VALUES (new.id, {new_vals}); END",
            f"CREATE TRIGGER IF NOT EXISTS {table}_fts_delete AFTER DELETE ON {table} BEGIN "
            f"INSERT INTO {fts}({fts}, rowid, {cols}) VALUES ('delete', old.id, {old_vals}); END",
            f"CREATE TRIGGER IF NOT EXISTS {table}_fts_update AFTER UPDATE ON {table} BEGIN "
            f"INSERT INTO {fts}({fts}, rowid, {cols}) VALUES ('delete', old.id, {old_vals}); "
            f"INSERT INTO {fts}(rowid, {cols}) VALUES (new.id, {new_vals}); END",
        ]
    return statements


def ensure_fts(engine: Engine) -> None:
    with engine.begin() as connection:
        for statement in _fts_statements():
            connection.execute(text(statement))


def to_match_expression(query: str) -> str:
    """Sanitize a free-text query into an FTS5 MATCH expression.

    User queries may contain FTS5 operators or punctuation that breaks the
    parser; quote each token and OR them so bm25 ranking does the work.
    """
    tokens = re.findall(r"[A-Za-z0-9_]+", query)
    return " OR ".join(f'"{token}"' for token in tokens)


def search_notes(session: Session, workspace_id: int, query: str, limit: int = 10) -> list[dict]:
    return _search(
        session,
        """
        SELECT n.path AS ref, n.title AS title,
               snippet(notes_fts, -1, '', '', '…', 12) AS snippet,
               bm25(notes_fts) AS score
        FROM notes_fts JOIN notes n ON n.id = notes_fts.rowid
        WHERE notes_fts MATCH :match AND n.workspace_id = :workspace_id
        ORDER BY bm25(notes_fts) LIMIT :limit
        """,
        workspace_id,
        query,
        limit,
    )


def search_memories(session: Session, workspace_id: int, query: str, limit: int = 10) -> list[dict]:
    return _search(
        session,
        """
        SELECT 'memory:' || m.id AS ref,
               coalesce(m.summary, substr(m.content, 1, 80)) AS title,
               snippet(memories_fts, -1, '', '', '…', 12) AS snippet,
               bm25(memories_fts) AS score,
               m.state AS state,
               m.created_at AS created_at,
               m.confidence AS confidence
        FROM memories_fts JOIN memories m ON m.id = memories_fts.rowid
        WHERE memories_fts MATCH :match AND m.workspace_id = :workspace_id
          AND m.state != 'rejected'
        ORDER BY bm25(memories_fts) LIMIT :limit
        """,
        workspace_id,
        query,
        limit,
    )


def search_sources(session: Session, workspace_id: int, query: str, limit: int = 10) -> list[dict]:
    return _search(
        session,
        """
        SELECT 'source:' || s.id AS ref, s.title AS title,
               snippet(sources_fts, -1, '', '', '…', 12) AS snippet,
               bm25(sources_fts) AS score
        FROM sources_fts JOIN sources s ON s.id = sources_fts.rowid
        WHERE sources_fts MATCH :match AND s.workspace_id = :workspace_id
        ORDER BY bm25(sources_fts) LIMIT :limit
        """,
        workspace_id,
        query,
        limit,
    )


def _search(session: Session, sql: str, workspace_id: int, query: str, limit: int) -> list[dict]:
    match = to_match_expression(query)
    if not match:
        return []
    rows = session.execute(
        text(sql), {"match": match, "workspace_id": workspace_id, "limit": limit}
    )
    return [dict(row._mapping) for row in rows]
