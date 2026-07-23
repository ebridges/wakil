---
title: Opinion Memory Type as a Free-String Addition; Confidence Becomes a Retrieval-Ranking Input
status: accepted
date: 2026-07-22
audience: wakil design
---

# Opinion Memory Type as a Free-String Addition; Confidence Becomes a Retrieval-Ranking Input

## Context

A third-party proposal (`docs/memory-opinion-and-register.md`, never
committed) identified two real gaps in memory extraction: value judgments
and interpretations were being tagged `fact` for lack of a better type, and
casually-asserted 1:1 claims ("hot takes") had no way to be marked as
lower-commitment than a confidently-stated fact.

The proposal's own framing overstated what two existing mechanisms already
did for the hot-take case. Lifecycle `state` does no new work here: every
memory lands in `candidate` unconditionally at ingest
(`src/wakil/app/ingest_service.py`), and only a human `wakil memory
promote|reject|archive` ever changes it — nothing about a memory's type or
confidence has ever influenced that. And `confidence`
(`CandidateMemoryModel.confidence`, `Memory.confidence`) was, before this
change, write-only and display-only: `search_memories()`
(`src/wakil/storage/fts.py`) never even `SELECT`ed the column, and
`retrieval_rank()`/`_ranked_memory_hits()`
(`src/wakil/app/memory_service.py`, `src/wakil/app/search_service.py`) never
read it. So "confidence models hot-take-ness" was aspirational until it was
actually wired into ranking.

`Memory.memory_type` is `String(30)` with no DB enum or CHECK constraint
(`src/wakil/storage/schema.py`), and ADR 0005 already established the
precedent of adding new `memory_type` values (`event`) to that free-string
column with no migration, reusing the existing Memory lifecycle rather than
forking a new one.

## Decision

- `opinion` is added to `CandidateMemoryModel.type`'s `Field` description
  vocabulary — a free-string value addition on an already-free-string
  column, requiring no migration, following the same pattern ADR 0005
  established for `memory_type='event'`.
- `CandidateMemoryModel.confidence` gains `Field(ge=0.0, le=1.0)` —
  previously unconstrained at both the Pydantic and DB layer, tightened
  because its semantic weight (a probability-like scale used to signal
  low-commitment claims) just increased.
- `confidence` becomes a ranking input for the first time:
  `search_memories()` now selects it, and `retrieval_rank()` /
  `_ranked_memory_hits()` use it as a same-state tiebreak — added as a
  fractional offset that never crosses a `_STATE_RANK` boundary, so a
  low-confidence `durable` memory still outranks every `candidate` memory.
  Memories with `confidence IS NULL` (most existing rows, since the field
  was inert before this change) are treated as neutral (`0.5`) for ranking
  purposes, so the existing untagged corpus isn't reshuffled on day one.
- Lifecycle `state` is explicitly **unchanged** by this work: promotion,
  rejection, and archival remain 100% manual via `wakil memory
  promote|reject|archive`; no automatic state transition is introduced
  based on type or confidence. The extractor's `≤0.4` confidence guidance
  for hot takes (`src/wakil/skills/transcript/SKILL.md`) is prompt-only, not
  enforced in code — matching the existing precedent that
  `_clamp01()` (`src/wakil/app/ingest_service.py`) already accepts any
  in-range value without further validation.

## Consequences

- No migration: `memory_type` and `confidence` are both already
  unconstrained-shape columns; `opinion` rows work with every existing
  filter (`wakil memory list --type opinion`) and display path unmodified.
- `state` remains the sole durability signal a human controls directly;
  `confidence` only ever reorders memories that already share a state, and
  never determines durability itself.
- The fact/opinion boundary (especially unestablished causal "because"
  clauses) is judgment-call territory for the extractor model and is not
  perfectly consistent across ingests — covered by a new
  `src/wakil/skills/transcript/eval.json` scenario
  (`hypothesis-vs-opinion-because-clause`) rather than a hard rule, per ADR
  0004's live-model eval gating.
- An explicit `register`/`stance` field (the proposal's "phase 2") remains
  deferred; ship this first and revisit only if confidence + state prove
  insufficient in practice.

## Sources

- `src/wakil/llm/schemas.py` (`CandidateMemoryModel`)
- `src/wakil/app/memory_service.py` (`retrieval_rank`)
- `src/wakil/storage/fts.py` (`search_memories`)
- `docs/adr/0005-timeline-entries-as-memory-rows.md` (free-string
  `memory_type` precedent)
- `docs/memory-opinion-and-register.md` (originating, non-authoritative
  proposal)
