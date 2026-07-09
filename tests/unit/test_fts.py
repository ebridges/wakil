from pathlib import Path

from sqlalchemy import select

from wakil.app.workspace_service import init_workspace, open_session
from wakil.config.settings import WorkspaceConfig
from wakil.storage import fts
from wakil.storage.schema import Memory, Source, User, Workspace


def _workspace_id(session) -> int:
    return session.scalar(select(Workspace.id))


def test_notes_are_searchable_after_indexing(kb_path: Path):
    init_workspace(kb_path)
    config = WorkspaceConfig.load(kb_path)
    with open_session(config) as session:
        rows = fts.search_notes(session, _workspace_id(session), "graph memory")
    assert any(row["ref"] == "concepts/graph-memory.md" for row in rows)


def test_note_updates_and_deletes_stay_in_sync(kb_path: Path):
    init_workspace(kb_path)
    (kb_path / "concepts" / "graph-memory.md").unlink()
    (kb_path / "drafts" / "quantum.md").write_text("# Quantum Routing\n")
    init_workspace(kb_path)

    config = WorkspaceConfig.load(kb_path)
    with open_session(config) as session:
        ws = _workspace_id(session)
        assert not any(
            row["ref"] == "concepts/graph-memory.md"
            for row in fts.search_notes(session, ws, "graph memory")
        )
        assert any(
            row["ref"] == "drafts/quantum.md"
            for row in fts.search_notes(session, ws, "quantum routing")
        )


def test_memories_and_sources_are_searchable(kb_path: Path):
    init_workspace(kb_path)
    config = WorkspaceConfig.load(kb_path)
    with open_session(config) as session:
        ws = _workspace_id(session)
        user_id = session.scalar(select(User.id))
        session.add(
            Memory(
                workspace_id=ws,
                user_id=user_id,
                memory_type="fact",
                content="FNOL routing decisions are made by the triage team.",
            )
        )
        session.add(
            Source(workspace_id=ws, source_type="article", title="Insurance claims automation")
        )
        session.commit()

        memories = fts.search_memories(session, ws, "FNOL routing")
        sources = fts.search_sources(session, ws, "claims automation")
    assert len(memories) == 1
    assert memories[0]["ref"].startswith("memory:")
    assert len(sources) == 1
    assert sources[0]["ref"].startswith("source:")


def test_rejected_memories_are_excluded(kb_path: Path):
    init_workspace(kb_path)
    config = WorkspaceConfig.load(kb_path)
    with open_session(config) as session:
        ws = _workspace_id(session)
        user_id = session.scalar(select(User.id))
        session.add(
            Memory(
                workspace_id=ws,
                user_id=user_id,
                memory_type="fact",
                content="A rejected claim about zebras.",
                state="rejected",
            )
        )
        session.commit()
        assert fts.search_memories(session, ws, "zebras") == []


def test_match_expression_sanitizes_operators():
    assert (
        fts.to_match_expression('routing AND "claims" (x)')
        == '"routing" OR "AND" OR "claims" OR "x"'
    )
    assert fts.to_match_expression("!!!") == ""
