"""Memory lifecycle: review, promotion, rejection, archiving, and fading.

Lifecycle (from the build plan):

    working → candidate → durable
                      ↘ rejected
    durable → archived

Rather than deleting memories, retrieval downranks them: durable memories are
favored, candidates stay visible, working memories fade with age, archived
memories remain searchable but sink to the bottom, and rejected memories are
excluded from search entirely (enforced in the FTS layer).
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from wakil.storage.schema import Memory, utcnow

MEMORY_STATES = ("working", "candidate", "durable", "rejected", "archived")

# state → allowed target states for explicit user transitions
_TRANSITIONS: dict[str, set[str]] = {
    "working": {"candidate", "durable", "rejected", "archived"},
    "candidate": {"durable", "rejected", "archived"},
    "durable": {"archived"},
    "archived": set(),
    "rejected": set(),
}

# Retrieval ordering: lower rank sorts first. Working memories older than
# this fade behind fresh ones but stay ahead of archived material.
_STATE_RANK = {"durable": 0.0, "candidate": 1.0, "working": 2.0, "archived": 4.0}
WORKING_FADE_DAYS = 30
_FADED_WORKING_RANK = 3.0


class MemoryError(RuntimeError):
    pass


@dataclass
class TransitionResult:
    memory_id: int
    old_state: str
    new_state: str


def list_memories(
    session: Session,
    workspace_id: int,
    state: str | None = None,
    memory_type: str | None = None,
    limit: int = 50,
) -> list[Memory]:
    stmt = select(Memory).where(Memory.workspace_id == workspace_id)
    if state is not None:
        if state not in MEMORY_STATES:
            raise MemoryError(f"Unknown state: {state} (expected one of {MEMORY_STATES})")
        stmt = stmt.where(Memory.state == state)
    if memory_type is not None:
        stmt = stmt.where(Memory.memory_type == memory_type)
    stmt = stmt.order_by(Memory.created_at.desc()).limit(limit)
    return list(session.scalars(stmt))


def get_memory(session: Session, workspace_id: int, memory_id: int) -> Memory:
    memory = session.get(Memory, memory_id)
    if memory is None or memory.workspace_id != workspace_id:
        raise MemoryError(f"No memory with id {memory_id} in this workspace.")
    return memory


def transition_memories(
    session: Session, workspace_id: int, memory_ids: list[int], new_state: str
) -> list[TransitionResult]:
    """Move memories to a new lifecycle state, enforcing valid transitions."""
    if new_state not in MEMORY_STATES:
        raise MemoryError(f"Unknown state: {new_state}")
    results = []
    for memory_id in memory_ids:
        memory = get_memory(session, workspace_id, memory_id)
        if memory.state == new_state:
            raise MemoryError(f"Memory {memory_id} is already {new_state}.")
        if new_state not in _TRANSITIONS[memory.state]:
            raise MemoryError(f"Memory {memory_id} is {memory.state}; cannot move to {new_state}.")
        results.append(
            TransitionResult(memory_id=memory_id, old_state=memory.state, new_state=new_state)
        )
        memory.state = new_state
        memory.last_seen_at = utcnow()
    return results


def retrieval_rank(state: str, created_at: datetime | None) -> float:
    """Lower ranks first. Encodes the fading rules described above."""
    rank = _STATE_RANK.get(state, 2.0)
    if state == "working" and created_at is not None:
        created = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
        age_days = (datetime.now(UTC) - created).days
        if age_days > WORKING_FADE_DAYS:
            rank = _FADED_WORKING_RANK
    return rank


def touch_memories(session: Session, memory_ids: list[int]) -> None:
    """Record that these memories were just used (feeds future ranking)."""
    now = utcnow()
    for memory_id in memory_ids:
        memory = session.get(Memory, memory_id)
        if memory is not None:
            memory.last_seen_at = now
