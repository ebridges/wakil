"""Traversal queries over the `relationships` table (ADR 0006 Phase 2).

Answers `wakil relationships <note-path>`-style questions: from an anchor
note, walk `mentions` (and other Note↔Note) edges outward/inward/both, up
to N hops, optionally filtered by predicate. Backed by a single SQLite
`WITH RECURSIVE` CTE — no graph library, no new table, no LLM call
(CLAUDE.md: "avoid premature graph databases"; the proposal's stated
design bias for this feature).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import TextClause, bindparam, text
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

Direction = Literal["out", "in", "both"]

# Ships as the default and hard cap on `--depth`. Chosen to match gbrain's
# old default (proposal's suggestion) — safe on a densely-linked kb, easy
# to raise later if it proves too tight. If a user asks for something
# larger we clamp to this rather than silently letting the CTE explode.
MAX_TRAVERSAL_DEPTH = 5


class TraversalError(Exception):
    """Raised when the anchor note can't be resolved or an argument is invalid."""


@dataclass(frozen=True)
class TraversalHit:
    """One reachable note in a traversal result.

    `depth` is the shortest hop count from the anchor at which this note
    was first reached (1 = a direct edge). `via_predicate` and `direction`
    describe the edge that first surfaced it.
    """

    note_id: int
    path: str
    title: str | None
    depth: int
    via_predicate: str
    # 'out' if anchor→hit only, 'in' if hit→anchor only, 'both' if the note
    # is reachable both ways at this same depth (mutual link) — surfaces
    # bidirectionality that the underlying walk edges alone would hide.
    direction: Direction


@dataclass(frozen=True)
class TraversalResult:
    anchor_path: str
    anchor_title: str | None
    direction: Direction
    predicate: str | None
    depth: int  # the depth we actually walked to (post-clamp)
    hits: list[TraversalHit]


def _validate_and_clamp_depth(depth: int, direction: str) -> int:
    """Validate `depth`/`direction` and clamp depth to `MAX_TRAVERSAL_DEPTH`."""
    if depth < 1:
        raise TraversalError(f"depth must be >= 1 (got {depth})")
    if direction not in ("out", "in", "both"):
        raise TraversalError(
            f"direction must be one of out|in|both (got {direction!r})"
        )
    return min(depth, MAX_TRAVERSAL_DEPTH)


def _resolve_anchor(
    session: Session, workspace_id: int, anchor_path: str
) -> tuple[int, str | None]:
    """Look up the anchor note's id and title, or raise `TraversalError`."""
    anchor_row = session.execute(
        text("SELECT id, title FROM notes WHERE workspace_id = :ws AND path = :path"),
        {"ws": workspace_id, "path": anchor_path},
    ).one_or_none()
    if anchor_row is None:
        raise TraversalError(f"no note in this workspace at path {anchor_path!r}")
    anchor_id, anchor_title = anchor_row
    return anchor_id, anchor_title


def _build_traversal_sql(direction: Direction) -> TextClause:
    """Build the single `WITH RECURSIVE` traversal query for `direction`.

    One recursive CTE, two seed halves (outgoing / incoming), unioned by
    direction. We keep the shortest hop count per reached note by
    aggregating in the outer SELECT — SQLite's UNION in a recursive CTE
    dedupes exact rows, so per-hit MIN(depth) gives us the shortest path.
    """
    seeds: list[str] = []
    if direction in ("out", "both"):
        seeds.append(
            "SELECT r.object_note_id AS note_id, 1 AS depth, r.predicate AS via_predicate, "
            "'out' AS direction "
            "FROM relationships r "
            "WHERE r.workspace_id = :ws "
            "  AND r.subject_note_id = :anchor_id "
            "  AND r.object_note_id IS NOT NULL "
            "  AND (:predicate IS NULL OR r.predicate = :predicate)"
        )
    if direction in ("in", "both"):
        seeds.append(
            "SELECT r.subject_note_id AS note_id, 1 AS depth, r.predicate AS via_predicate, "
            "'in' AS direction "
            "FROM relationships r "
            "WHERE r.workspace_id = :ws "
            "  AND r.object_note_id = :anchor_id "
            "  AND r.subject_note_id IS NOT NULL "
            "  AND (:predicate IS NULL OR r.predicate = :predicate)"
        )
    seed_sql = " UNION ".join(seeds)

    steps: list[str] = []
    if direction in ("out", "both"):
        steps.append(
            "SELECT r.object_note_id, w.depth + 1, r.predicate, 'out' "
            "FROM walk w JOIN relationships r "
            "  ON r.subject_note_id = w.note_id "
            "WHERE r.workspace_id = :ws "
            "  AND r.object_note_id IS NOT NULL "
            "  AND r.object_note_id != :anchor_id "
            "  AND w.depth < :depth "
            "  AND (:predicate IS NULL OR r.predicate = :predicate)"
        )
    if direction in ("in", "both"):
        steps.append(
            "SELECT r.subject_note_id, w.depth + 1, r.predicate, 'in' "
            "FROM walk w JOIN relationships r "
            "  ON r.object_note_id = w.note_id "
            "WHERE r.workspace_id = :ws "
            "  AND r.subject_note_id IS NOT NULL "
            "  AND r.subject_note_id != :anchor_id "
            "  AND w.depth < :depth "
            "  AND (:predicate IS NULL OR r.predicate = :predicate)"
        )
    step_sql = " UNION ".join(steps)

    # Two-step aggregation: `walk` collects every reachable (note, depth,
    # predicate, direction) tuple; `best_depth` picks the shortest hop
    # per note; the outer SELECT joins back to `walk` at that depth and
    # collapses to one row per note — predicate by lexicographic tie-break,
    # direction promoted to 'both' when the note is reachable both ways at
    # the same depth (otherwise the singleton). Doing this all in one
    # subquery hits SQLite's "misuse of aggregate MIN()" — hence the split.
    return text(
        f"""
        WITH RECURSIVE walk(note_id, depth, via_predicate, direction) AS (
            {seed_sql}
            UNION
            {step_sql}
        ),
        best_depth AS (
            SELECT note_id, MIN(depth) AS depth FROM walk GROUP BY note_id
        )
        SELECT n.id, n.path, n.title, b.depth,
               (SELECT MIN(w.via_predicate) FROM walk w
                  WHERE w.note_id = n.id AND w.depth = b.depth) AS via_predicate,
               CASE
                 WHEN (SELECT COUNT(DISTINCT w.direction) FROM walk w
                         WHERE w.note_id = n.id AND w.depth = b.depth) > 1
                 THEN 'both'
                 ELSE (SELECT MIN(w.direction) FROM walk w
                         WHERE w.note_id = n.id AND w.depth = b.depth)
               END AS direction
        FROM best_depth b JOIN notes n ON n.id = b.note_id
        WHERE n.workspace_id = :ws
        ORDER BY b.depth, n.path
        """
    ).bindparams(
        bindparam("ws"),
        bindparam("anchor_id"),
        bindparam("depth"),
        bindparam("predicate"),
    )


def _hits_from_rows(rows: Sequence[Row]) -> list[TraversalHit]:
    """Map raw `(id, path, title, depth, via_predicate, direction)` rows."""
    return [
        TraversalHit(
            note_id=row[0],
            path=row[1],
            title=row[2],
            depth=row[3],
            via_predicate=row[4],
            direction=row[5],
        )
        for row in rows
    ]


def traverse(
    session: Session,
    workspace_id: int,
    anchor_path: str,
    *,
    direction: Direction = "both",
    predicate: str | None = None,
    depth: int = 1,
) -> TraversalResult:
    """Walk Note↔Note edges from `anchor_path` out to `depth` hops.

    Raises `TraversalError` if the anchor doesn't resolve to a real note or
    if arguments are out of range. Depth is clamped to `MAX_TRAVERSAL_DEPTH`
    to guard against runaway queries on a densely-linked kb.
    """
    depth = _validate_and_clamp_depth(depth, direction)
    anchor_id, anchor_title = _resolve_anchor(session, workspace_id, anchor_path)

    sql = _build_traversal_sql(direction)
    rows = session.execute(
        sql,
        {
            "ws": workspace_id,
            "anchor_id": anchor_id,
            "depth": depth,
            "predicate": predicate,
        },
    ).all()

    return TraversalResult(
        anchor_path=anchor_path,
        anchor_title=anchor_title,
        direction=direction,
        predicate=predicate,
        depth=depth,
        hits=_hits_from_rows(rows),
    )
