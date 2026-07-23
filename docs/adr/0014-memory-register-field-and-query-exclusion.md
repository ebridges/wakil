---
title: Explicit register/commitment Field on Memory, Excluded from Query Grounding by Default
status: accepted
date: 2026-07-23
audience: wakil design
---

# Explicit register/commitment Field on Memory, Excluded from Query Grounding by Default

## Context

Phase 1 (ADR 0013) modeled "hot takes" — casually-asserted, low-commitment
claims — indirectly, via low `confidence` and the (unaffected) `candidate`
lifecycle state. That phase was scoped explicitly as an interim measure:
`docs/memory-opinion-and-register.md`'s "Optional phase 2" proposed an
explicit `register: Literal["formal", "casual"]` field once phase 1 alone
proved insufficient to keep hot takes from grounding answers with the same
weight as a confidently-stated fact.

This phase adds that field, per the originating proposal's phase 2 scope:
an explicit, purpose-built axis for commitment/register, kept orthogonal to
`memory_type` (a `casual` `opinion` and a `casual` `fact` are both valid),
and orthogonal to `confidence` (register is about the setting a claim was
asserted in, not how certain the extractor is that it's accurately stated).

## Decision

- `CandidateMemoryModel` and `Memory` gain a nullable `formal|casual` field,
  migrated via `storage/migrations/versions/0005_memory_stance.py` (a real
  schema change, unlike phase 1's free-string `opinion` type addition).
- **Named `stance`, not `register`.** Pydantic's `ModelMetaclass` inherits
  from `abc.ABCMeta`, which defines a `register` classmethod (used for
  registering virtual subclasses); a Pydantic field literally named
  `register` shadows it, and Pydantic warns on every model class
  definition (`UserWarning: Field name "register" ... shadows an attribute
  in parent "BaseModel"`). Since `CandidateMemoryModel` is a `BaseModel`,
  the field is named `stance` there and, for consistency (no translation
  layer at the ORM boundary), on `Memory.stance` and the internal
  `CandidateMemory` dataclass in `ingest_service.py` too. The CLI and
  display layer keep the user-facing vocabulary as `register` (flag
  `wakil memory list --register`, table/detail column "Register") —
  mirroring the existing precedent of `--type` mapping to the Python
  identifier `memory_type` (avoiding a collision with the `type` builtin).
  See `docs/TROUBLESHOOTING.md` for the underlying gotcha.
- The transcript extractor (`skills/transcript/SKILL.md`) sets
  `stance="casual"` for the same class of claims that already get low
  `confidence` under phase 1 guidance — the two signals are set together,
  not as alternatives.
- `wakil query` excludes `stance="casual"` memories from the answer's
  grounding context by default (`_load_text` in `app/query_service.py`
  returns `None` for them unless `include_casual=True`), via a new
  `--include-casual` CLI flag. This is exclusion, not a ranking weight —
  simpler than blending a third signal into `retrieval_rank()`'s numeric
  tiebreak, and matches the proposal's "downranks/excludes ... unless
  explicitly asked" phrasing with the simpler of the two options.
  `wakil search` is untouched: hot takes still surface there, since search
  is about finding things (including hot takes) rather than grounding a
  synthesized answer.
- `wakil memory list --register formal|casual` and the `Register` column in
  `wakil memory show`/`list` mirror the existing `--type`/`Type` filter and
  display pattern, for the same human-review reasons ADR 0013 already
  established for `opinion`.

## Consequences

- One real migration (`0005`), unlike phase 1's free-string addition —
  justified per the original proposal's own criterion ("only if phase 1
  proves insufficient"), since confidence-as-tiebreak alone still let a
  hot take ground a query answer, just ranked slightly behind a confident
  fact rather than excluded from it.
- `stance`/`register` naming asymmetry (Python/DB identifier vs. CLI/display
  vocabulary) is a deliberate, precedented choice, not an oversight — see
  the `--type`/`memory_type` precedent above.
- `search` and `query` now diverge in what they surface for the same stored
  data (search still shows casual hot takes; query's default answer
  grounding does not) — an intentional, minimal behavior split rather than
  a new ranking dimension threaded through both.

## Sources

- `docs/adr/0013-opinion-memory-type-and-confidence-ranking.md` (phase 1)
- `docs/memory-opinion-and-register.md` (originating, non-authoritative
  proposal, "Optional phase 2")
- `src/wakil/llm/schemas.py` (`CandidateMemoryModel.stance`)
- `src/wakil/app/query_service.py` (`_load_text`, `run_query`)
- `src/wakil/storage/migrations/versions/0005_memory_stance.py`
