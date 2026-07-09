from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from wakil.app import memory_service
from wakil.app.memory_service import (
    MemoryError,
    list_memories,
    retrieval_rank,
    transition_memories,
)
from wakil.app.search_service import search_workspace
from wakil.app.workspace_service import init_workspace, open_session
from wakil.config.settings import WorkspaceConfig
from wakil.storage.schema import Memory, User, Workspace


@pytest.fixture
def workspace(kb_path: Path) -> WorkspaceConfig:
    init_workspace(kb_path)
    return WorkspaceConfig.load(kb_path)


def _add_memory(session, workspace_id, content, state="candidate", created_at=None) -> int:
    user_id = session.scalar(select(User.id))
    memory = Memory(
        workspace_id=workspace_id,
        user_id=user_id,
        memory_type="fact",
        content=content,
        state=state,
    )
    if created_at is not None:
        memory.created_at = created_at
    session.add(memory)
    session.flush()
    return memory.id


def test_list_memories_filters_by_state(workspace):
    with open_session(workspace) as session:
        ws = session.scalar(select(Workspace.id))
        _add_memory(session, ws, "a candidate", state="candidate")
        _add_memory(session, ws, "a durable", state="durable")
        session.commit()

        assert len(list_memories(session, ws)) == 2
        candidates = list_memories(session, ws, state="candidate")
        assert [m.content for m in candidates] == ["a candidate"]
        with pytest.raises(MemoryError, match="Unknown state"):
            list_memories(session, ws, state="bogus")


def test_valid_transitions(workspace):
    with open_session(workspace) as session:
        ws = session.scalar(select(Workspace.id))
        candidate = _add_memory(session, ws, "x", state="candidate")
        session.commit()

        results = transition_memories(session, ws, [candidate], "durable")
        session.commit()
        assert results[0].old_state == "candidate"
        assert session.get(Memory, candidate).state == "durable"
        assert session.get(Memory, candidate).last_seen_at is not None

        results = transition_memories(session, ws, [candidate], "archived")
        session.commit()
        assert session.get(Memory, candidate).state == "archived"


def test_invalid_transitions_rejected(workspace):
    with open_session(workspace) as session:
        ws = session.scalar(select(Workspace.id))
        durable = _add_memory(session, ws, "x", state="durable")
        rejected = _add_memory(session, ws, "y", state="rejected")
        session.commit()

        with pytest.raises(MemoryError, match="cannot move"):
            transition_memories(session, ws, [durable], "rejected")
        with pytest.raises(MemoryError, match="cannot move"):
            transition_memories(session, ws, [rejected], "durable")
        with pytest.raises(MemoryError, match="already durable"):
            transition_memories(session, ws, [durable], "durable")
        with pytest.raises(MemoryError, match="No memory with id"):
            transition_memories(session, ws, [9999], "durable")


def test_transition_is_all_or_nothing_per_call(workspace):
    with open_session(workspace) as session:
        ws = session.scalar(select(Workspace.id))
        good = _add_memory(session, ws, "ok", state="candidate")
        bad = _add_memory(session, ws, "no", state="rejected")
        session.commit()

        with pytest.raises(MemoryError):
            transition_memories(session, ws, [good, bad], "durable")
        session.rollback()
        assert session.get(Memory, good).state == "candidate"


def test_retrieval_rank_orders_states():
    now = datetime.now(UTC)
    durable = retrieval_rank("durable", now)
    candidate = retrieval_rank("candidate", now)
    working = retrieval_rank("working", now)
    faded = retrieval_rank("working", now - timedelta(days=45))
    archived = retrieval_rank("archived", now)
    assert durable < candidate < working < faded < archived


def test_search_orders_memories_by_lifecycle(workspace):
    old = datetime.now(UTC) - timedelta(days=60)
    with open_session(workspace) as session:
        ws = session.scalar(select(Workspace.id))
        _add_memory(session, ws, "zugzwang insight (working, old)", state="working", created_at=old)
        _add_memory(session, ws, "zugzwang insight (archived)", state="archived")
        _add_memory(session, ws, "zugzwang insight (durable)", state="durable")
        _add_memory(session, ws, "zugzwang insight (candidate)", state="candidate")
        session.commit()

        hits = [h for h in search_workspace(session, workspace, "zugzwang") if h.kind == "memory"]
    states = [h.state for h in hits]
    assert states == ["durable", "candidate", "working", "archived"]


def test_rejected_memories_never_surface(workspace):
    with open_session(workspace) as session:
        ws = session.scalar(select(Workspace.id))
        _add_memory(session, ws, "xylophone secret", state="rejected")
        session.commit()
        hits = search_workspace(session, workspace, "xylophone")
    assert [h for h in hits if h.kind == "memory"] == []


def test_touch_memories_updates_last_seen(workspace):
    with open_session(workspace) as session:
        ws = session.scalar(select(Workspace.id))
        memory_id = _add_memory(session, ws, "touched", state="durable")
        session.commit()
        assert session.get(Memory, memory_id).last_seen_at is None
        memory_service.touch_memories(session, [memory_id, 9999])  # unknown id ignored
        session.commit()
        assert session.get(Memory, memory_id).last_seen_at is not None
