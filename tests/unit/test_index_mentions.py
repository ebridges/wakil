"""Index-time wikilink extraction into `mentions` Relationship rows.

Covers ADR 0006 Phase 1 acceptance criteria (Phase 1 section of
`docs/relationship-graph-traversal-proposal.md`): fresh index populates
edges; unchanged reindex is a no-op; removed link prunes its row; both
`[[people/x]]` and `[[sources/y.md]]` forms resolve; a dead-link target
is skipped cleanly.
"""

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import aliased

from wakil.app.workspace_service import (
    MENTIONS_PREDICATE,
    index_notes,
    init_workspace,
    open_session,
)
from wakil.config.settings import WorkspaceConfig
from wakil.storage.schema import Note, Relationship, Workspace


def _write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _mentions(session, workspace_id: int) -> list[tuple[str, str]]:
    """Every (subject.path, object.path) `mentions` edge in the workspace."""
    subject = aliased(Note)
    obj = aliased(Note)
    rows = session.execute(
        select(subject.path, obj.path)
        .join(Relationship, Relationship.subject_note_id == subject.id)
        .join(obj, obj.id == Relationship.object_note_id)
        .where(
            Relationship.workspace_id == workspace_id,
            Relationship.predicate == MENTIONS_PREDICATE,
        )
        .order_by(subject.path, obj.path)
    ).all()
    return [(r[0], r[1]) for r in rows]


def _reindex(config: WorkspaceConfig, root: Path) -> None:
    with open_session(config) as session:
        ws = session.scalar(select(Workspace).where(Workspace.root_path == str(config.state_root)))
        index_notes(session, ws.id, root)
        session.commit()


def test_fresh_index_populates_mentions_edges(tmp_path: Path):
    root = tmp_path / "kb"
    _write(root, "people/alice.md", "# Alice\n\nMet with [[people/bob]] last week.\n")
    _write(root, "people/bob.md", "# Bob\n\nColleague of [[people/alice|Alice]].\n")

    init_workspace(root)

    config = WorkspaceConfig.load(root)
    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        assert set(_mentions(session, ws)) == {
            ("people/alice.md", "people/bob.md"),
            ("people/bob.md", "people/alice.md"),
        }


def test_unchanged_reindex_is_a_noop(tmp_path: Path):
    root = tmp_path / "kb"
    _write(root, "people/alice.md", "# Alice\n\nMet with [[people/bob]].\n")
    _write(root, "people/bob.md", "# Bob\n")

    init_workspace(root)
    config = WorkspaceConfig.load(root)
    with open_session(config) as session:
        first = list(session.scalars(select(Relationship.id).order_by(Relationship.id)))

    _reindex(config, root)
    with open_session(config) as session:
        second = list(session.scalars(select(Relationship.id).order_by(Relationship.id)))

    assert first == second  # no churn: same row ids, no delete+insert cycle.


def test_removed_link_prunes_its_row(tmp_path: Path):
    root = tmp_path / "kb"
    _write(root, "people/alice.md", "# Alice\n\nRefs [[people/bob]] and [[people/carol]].\n")
    _write(root, "people/bob.md", "# Bob\n")
    _write(root, "people/carol.md", "# Carol\n")

    init_workspace(root)
    config = WorkspaceConfig.load(root)
    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        assert {edge[1] for edge in _mentions(session, ws)} == {
            "people/bob.md",
            "people/carol.md",
        }

    _write(root, "people/alice.md", "# Alice\n\nRefs [[people/bob]] only now.\n")
    _reindex(config, root)

    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        assert _mentions(session, ws) == [("people/alice.md", "people/bob.md")]


def test_both_wikilink_forms_resolve(tmp_path: Path):
    root = tmp_path / "kb"
    _write(root, "people/alice.md", "# Alice\n")
    _write(root, "sources/paper.md", "# Paper\n")
    _write(
        root,
        "concepts/topic.md",
        "# Topic\n\nSee [[people/alice]] and [[sources/paper.md]].\n",
    )

    init_workspace(root)

    config = WorkspaceConfig.load(root)
    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        assert set(_mentions(session, ws)) == {
            ("concepts/topic.md", "people/alice.md"),
            ("concepts/topic.md", "sources/paper.md"),
        }


def test_dead_link_target_is_skipped_cleanly(tmp_path: Path):
    root = tmp_path / "kb"
    _write(
        root,
        "people/alice.md",
        "# Alice\n\nRefs [[people/bob]] and [[people/nonexistent]].\n",
    )
    _write(root, "people/bob.md", "# Bob\n")

    # Must not raise: dead-link detection is `maintain`'s job, not indexing's.
    init_workspace(root)

    config = WorkspaceConfig.load(root)
    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        assert _mentions(session, ws) == [("people/alice.md", "people/bob.md")]


def test_note_only_row_leaves_memory_fks_null(tmp_path: Path):
    """Sanity: the shipped writer never fabricates dummy memory ids."""
    root = tmp_path / "kb"
    _write(root, "people/alice.md", "# Alice\n\nRefs [[people/bob]].\n")
    _write(root, "people/bob.md", "# Bob\n")
    init_workspace(root)

    config = WorkspaceConfig.load(root)
    with open_session(config) as session:
        edge = session.scalar(
            select(Relationship).where(Relationship.predicate == MENTIONS_PREDICATE)
        )
        assert edge is not None
        assert edge.subject_note_id is not None
        assert edge.object_note_id is not None
        assert edge.subject_memory_id is None
        assert edge.object_memory_id is None


def test_deleting_a_note_purges_edges_touching_it(tmp_path: Path):
    root = tmp_path / "kb"
    _write(root, "people/alice.md", "# Alice\n\nRefs [[people/bob]].\n")
    _write(root, "people/bob.md", "# Bob\n\nRefs [[people/alice]].\n")
    init_workspace(root)

    # Delete bob.md; alice's [[people/bob]] link now points at nothing,
    # and any incoming edge from bob must be gone.
    (root / "people" / "bob.md").unlink()
    _reindex(WorkspaceConfig.load(root), root)

    config = WorkspaceConfig.load(root)
    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        assert _mentions(session, ws) == []


def test_self_link_is_skipped(tmp_path: Path):
    """A note linking to itself doesn't create a degenerate self-edge."""
    root = tmp_path / "kb"
    _write(root, "concepts/index.md", "# Index\n\nSee [[concepts/index]] itself.\n")

    init_workspace(root)

    config = WorkspaceConfig.load(root)
    with open_session(config) as session:
        assert (
            session.scalar(
                select(Relationship).where(Relationship.predicate == MENTIONS_PREDICATE)
            )
            is None
        )
