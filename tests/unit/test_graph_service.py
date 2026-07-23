"""Traversal service: recursive CTE over Relationship rows.

Seeded against directly-inserted `Relationship` rows so the traversal is
tested independently of index_notes' extraction path.
"""

from pathlib import Path

import pytest
from sqlalchemy import select

from wakil.app.graph_service import MAX_TRAVERSAL_DEPTH, TraversalError, traverse
from wakil.app.workspace_service import init_workspace, open_session
from wakil.config.settings import WorkspaceConfig
from wakil.storage.schema import Note, Relationship, Workspace


def _kb(root: Path) -> WorkspaceConfig:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.md").write_text("# A\n")
    (root / "b.md").write_text("# B\n")
    (root / "c.md").write_text("# C\n")
    (root / "d.md").write_text("# D\n")
    init_workspace(root)
    return WorkspaceConfig.load(root)


def _seed_chain(config: WorkspaceConfig, *edges: tuple[str, str, str]) -> None:
    """Insert (subject_path, predicate, object_path) triples as note edges."""
    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        notes = {n.path: n.id for n in session.scalars(select(Note))}
        # Clear anything index_notes already put down so the test controls
        # the graph shape exactly.
        for row in list(session.scalars(select(Relationship))):
            session.delete(row)
        for subject, predicate, obj in edges:
            session.add(
                Relationship(
                    workspace_id=ws,
                    subject_note_id=notes[subject],
                    object_note_id=notes[obj],
                    predicate=predicate,
                )
            )
        session.commit()


def _paths(result) -> list[tuple[str, int]]:
    return [(hit.path, hit.depth) for hit in result.hits]


def test_out_traversal_one_hop(tmp_path: Path):
    config = _kb(tmp_path / "kb")
    _seed_chain(config, ("a.md", "mentions", "b.md"), ("a.md", "mentions", "c.md"))

    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        result = traverse(session, ws, "a.md", direction="out", depth=1)

    assert _paths(result) == [("b.md", 1), ("c.md", 1)]
    assert all(hit.direction == "out" for hit in result.hits)
    assert all(hit.via_predicate == "mentions" for hit in result.hits)


def test_in_traversal_finds_backlinks(tmp_path: Path):
    config = _kb(tmp_path / "kb")
    _seed_chain(
        config,
        ("a.md", "mentions", "c.md"),
        ("b.md", "mentions", "c.md"),
    )

    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        result = traverse(session, ws, "c.md", direction="in", depth=1)

    assert _paths(result) == [("a.md", 1), ("b.md", 1)]
    assert all(hit.direction == "in" for hit in result.hits)


def test_both_direction_merges_and_dedupes(tmp_path: Path):
    config = _kb(tmp_path / "kb")
    _seed_chain(
        config,
        ("a.md", "mentions", "b.md"),  # out from a
        ("b.md", "mentions", "a.md"),  # in to a via same peer
    )

    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        result = traverse(session, ws, "a.md", direction="both", depth=1)

    # b is reachable both ways at depth 1 — one row, marked 'both'.
    assert _paths(result) == [("b.md", 1)]
    assert [hit.direction for hit in result.hits] == ["both"]


def test_asymmetric_link_reports_the_singleton_direction(tmp_path: Path):
    """A one-way relationship in `both` mode reports 'in' or 'out', not 'both'."""
    config = _kb(tmp_path / "kb")
    _seed_chain(
        config,
        ("a.md", "mentions", "b.md"),  # only out-edge
        ("c.md", "mentions", "a.md"),  # only in-edge
    )

    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        result = traverse(session, ws, "a.md", direction="both", depth=1)

    by_path = {hit.path: hit.direction for hit in result.hits}
    assert by_path == {"b.md": "out", "c.md": "in"}


def test_multi_hop_walk(tmp_path: Path):
    config = _kb(tmp_path / "kb")
    _seed_chain(
        config,
        ("a.md", "mentions", "b.md"),
        ("b.md", "mentions", "c.md"),
        ("c.md", "mentions", "d.md"),
    )

    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        result = traverse(session, ws, "a.md", direction="out", depth=3)

    assert _paths(result) == [("b.md", 1), ("c.md", 2), ("d.md", 3)]


def test_depth_bound_cuts_walk(tmp_path: Path):
    config = _kb(tmp_path / "kb")
    _seed_chain(
        config,
        ("a.md", "mentions", "b.md"),
        ("b.md", "mentions", "c.md"),
        ("c.md", "mentions", "d.md"),
    )

    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        result = traverse(session, ws, "a.md", direction="out", depth=2)

    assert _paths(result) == [("b.md", 1), ("c.md", 2)]


def test_predicate_filter(tmp_path: Path):
    config = _kb(tmp_path / "kb")
    _seed_chain(
        config,
        ("a.md", "mentions", "b.md"),
        ("a.md", "works_at", "c.md"),
    )

    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        only_mentions = traverse(
            session, ws, "a.md", direction="out", depth=1, predicate="mentions"
        )
        only_works = traverse(
            session, ws, "a.md", direction="out", depth=1, predicate="works_at"
        )

    assert _paths(only_mentions) == [("b.md", 1)]
    assert _paths(only_works) == [("c.md", 1)]


def test_cycle_terminates_at_shortest_depth(tmp_path: Path):
    config = _kb(tmp_path / "kb")
    _seed_chain(
        config,
        ("a.md", "mentions", "b.md"),
        ("b.md", "mentions", "a.md"),
        ("b.md", "mentions", "c.md"),
    )

    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        result = traverse(session, ws, "a.md", direction="out", depth=5)

    # a→b (1), b→c (2). The b→a cycle back to the anchor is filtered out;
    # c is reported once at its shortest depth.
    assert _paths(result) == [("b.md", 1), ("c.md", 2)]


def test_missing_anchor_raises(tmp_path: Path):
    config = _kb(tmp_path / "kb")

    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        with pytest.raises(TraversalError, match="no note"):
            traverse(session, ws, "does/not/exist.md")


def test_bad_depth_raises(tmp_path: Path):
    config = _kb(tmp_path / "kb")

    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        with pytest.raises(TraversalError, match="depth"):
            traverse(session, ws, "a.md", depth=0)


def test_bad_direction_raises(tmp_path: Path):
    config = _kb(tmp_path / "kb")

    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        with pytest.raises(TraversalError, match="direction"):
            traverse(session, ws, "a.md", direction="sideways")  # type: ignore[arg-type]


def test_depth_is_clamped_to_max(tmp_path: Path):
    config = _kb(tmp_path / "kb")
    _seed_chain(config, ("a.md", "mentions", "b.md"))

    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        result = traverse(session, ws, "a.md", direction="out", depth=999)

    assert result.depth == MAX_TRAVERSAL_DEPTH


def test_note_only_edges_ignore_memory_only_rows(tmp_path: Path):
    """Memory↔Memory rows in the same table must not surface here."""
    config = _kb(tmp_path / "kb")
    # Insert a valid note↔note row plus a pure memory↔memory row —
    # traversal must ignore the memory-only edge.
    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        # Need a Memory row to satisfy FKs on subject/object_memory_id.
        from wakil.storage.schema import Memory, User
        user_id = session.scalar(select(User.id))
        memory = Memory(
            workspace_id=ws, user_id=user_id, memory_type="fact", content="x"
        )
        session.add(memory)
        session.flush()
        notes = {n.path: n.id for n in session.scalars(select(Note))}
        session.add(
            Relationship(
                workspace_id=ws,
                subject_note_id=notes["a.md"],
                object_note_id=notes["b.md"],
                predicate="mentions",
            )
        )
        session.add(
            Relationship(
                workspace_id=ws,
                subject_memory_id=memory.id,
                object_memory_id=memory.id,
                predicate="mentions",
            )
        )
        session.commit()

    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        result = traverse(session, ws, "a.md", direction="out", depth=1)

    assert _paths(result) == [("b.md", 1)]
