"""In-process cache bridging MCP prepare/apply tool-call pairs.

The CLI holds `prepare_capture()`'s (or `prepare_enrichment()`'s) returned
proposal as a local Python variable until `apply_capture()`/`apply_enrichment()`
runs moments later in the same process. MCP `prepare`/`apply` are two
separate tool calls that may happen in separate turns, so the proposal has
to be held here instead, keyed by a short-lived id the `prepare` tool hands
back to the client. Single-process, in-memory only: if the server restarts
mid-review, the client just calls `prepare` again — cheap for capture, one
re-run model call for enrichment, acceptable for a single-user local tool.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

DEFAULT_TTL_SECONDS = 60 * 60  # 1 hour


class ProposalNotFoundError(KeyError):
    """No pending proposal for this id (expired, wrong kind, or already applied)."""


@dataclass
class _Entry:
    kind: str
    payload: Any
    created_at: float = field(default_factory=time.monotonic)


class ProposalCache:
    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS):
        self._ttl = ttl_seconds
        self._entries: dict[str, _Entry] = {}

    def put(self, kind: str, payload: Any) -> str:
        self._evict_expired()
        proposal_id = uuid.uuid4().hex
        self._entries[proposal_id] = _Entry(kind=kind, payload=payload)
        return proposal_id

    def pop(self, kind: str, proposal_id: str) -> Any:
        self._evict_expired()
        entry = self._entries.pop(proposal_id, None)
        if entry is None or entry.kind != kind:
            raise ProposalNotFoundError(
                f"No pending {kind} proposal with id {proposal_id!r} — it may have "
                "expired or already been applied. Call the matching *_prepare tool again."
            )
        return entry.payload

    def _evict_expired(self) -> None:
        cutoff = time.monotonic() - self._ttl
        expired = [pid for pid, entry in self._entries.items() if entry.created_at < cutoff]
        for pid in expired:
            del self._entries[pid]
