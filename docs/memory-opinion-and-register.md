---
title: Add `opinion` memory type and a stance/register signal
status: proposal
audience: wakil coding agent
---

# Add an `opinion` memory type and a way to mark low-commitment "hot takes"

## Problem

Enrichment currently tags every extracted memory with a `type` drawn from
eight values (`fact | summary | relationship | question | hypothesis |
decision | theme | event`, see `src/wakil/llm/schemas.py:CandidateMemoryModel`).
Two real cases from actual transcript ingests don't fit:

1. **Opinions filed as facts.** Value judgments and interpretations are being
   captured as `fact`. Examples from a real ingest (source #3/#4 in the author's
   KB):
   - "the ~$630K compensation would be reasonable" — a value judgment, not a fact.
   - "Greenlite paused hiring *because* each additional engineer was expected
     to…" — the causal clause is interpretation, not an observed fact.
   `hypothesis` is the nearest existing type but means "tentative claim about
   the world," not "a subjective stance/value judgment." There is no home for
   an opinion.

2. **1:1 "hot takes."** In informal 1:1 conversation the speaker says things —
   self-reported metrics, provocative claims — they would not stand behind in a
   formal context. Example: "AI pushed our PR volume from ~30/week to N." That
   is not false, but it is a casual, unverified assertion the author does not
   want ranked as a durable fact. This is **orthogonal to type**: a hot take
   could be an opinion *or* a prediction. It's about *how strongly the claim is
   held / the register it was uttered in*, not *what kind of claim it is*.

## Two axes, kept separate

- **Type** = what the memory *is*. Add `opinion` here.
- **Commitment/register** = how strongly it's held / how formal the setting.
  This is the "hot take" axis and does NOT belong in the type enum.

Do not collapse these into one field. An opinion can be firmly held; a fact can
be a throwaway. Keeping them separate is what lets retrieval treat a
"firmly-held opinion" differently from a "hot-take fact."

## Scope decision (respect the prime directive)

`wakil`'s `CLAUDE.md` says build the smallest useful version and avoid
speculative abstraction. Therefore:

- **Do** add `opinion` to the type vocabulary. It's nearly free —
  `memory_type` is already `String(30)` in `storage/schema.py` (no DB enum, no
  migration needed) and the type list lives in a prompt/`Field` description.
- **Prefer** modeling the hot-take axis with mechanisms that already exist
  before adding a new column:
  - `confidence` (already on `CandidateMemoryModel` and the `Memory` row) —
    instruct the extractor to assign low confidence to casually-asserted /
    unverified 1:1 claims.
  - the **lifecycle state** — hot takes are exactly the things that should stay
    `candidate` or be `archived` (searchable, downranked) rather than promoted
    to `durable`.
- **Only if** confidence+state prove insufficient in practice, add an explicit
  optional `register` / `stance` field (see "Optional phase 2" below). Ship
  phase 1 first and see whether it's enough.

## Phase 1 — `opinion` type + extractor guidance (do this now)

1. **Schema.** In `src/wakil/llm/schemas.py`, add `opinion` to the
   `CandidateMemoryModel.type` description enumeration. New text:
   `"fact | opinion | summary | relationship | question | hypothesis |
   decision | theme | event"`. Define opinion in the description:
   *"opinion = a subjective value judgment, stance, or interpretation
   attributed to a speaker — distinct from fact (an observed/stated
   actuality)."*

2. **Extractor prompt.** In `src/wakil/llm/prompts.py` (the enrichment /
   extraction prompt builder) and in the transcript judgment skill
   `src/wakil/skills/transcript/SKILL.md`, add guidance:
   - Distinguish fact vs opinion: if a claim asserts what *is* (a number, an
     event, a stated position), it's `fact`; if it asserts what is *good/bad/
     worth-it/likely-caused-by* as a speaker's judgment, it's `opinion`.
     Causal "because" clauses that the transcript doesn't establish are
     interpretation → `opinion`.
   - Low-commitment register: when a claim is asserted casually in a 1:1 —
     an off-the-cuff metric, a provocative aside, something hedged or jokey —
     assign **low `confidence`** (guidance: ≤ 0.4) even if it's phrased as a
     fact. Do not silently upgrade a hot take to a confident fact.

3. **No migration.** `memory_type` is free-form `String(30)`; existing rows and
   reads are unaffected. `wakil memory list --type opinion` already works
   because the filter is a plain equality on the column
   (`app/memory_service.py`).

4. **Display.** Confirm `ui/console.py` renders an arbitrary `memory_type`
   string (it does today — it just prints `memory.memory_type`). No change
   needed, but eyeball the memory table/`show` output for an `opinion` row.

5. **Retrieval ranking (light touch).** Where memories are ranked for `query`
   (retrieval fade by state, per `README.md` "Memory lifecycle"), consider
   letting low-confidence memories rank below high-confidence ones within the
   same state, so hot takes surface last. Only if a ranking function already
   exists to extend — do not build a new ranker for this.

6. **Tests.** Add/extend:
   - a unit test that `CandidateMemoryModel` accepts `type="opinion"` and that
     a low-confidence value round-trips.
   - a memory-service test that `list(..., memory_type="opinion")` filters
     correctly.
   - if there's a skill eval for the transcript extractor, add a fixture case
     with one clear opinion and one hot-take-metric and assert the type /
     low-confidence outcome. (Skill evals are live-model and excluded from
     default CI per ADR 0004 — gate accordingly.)

## Optional phase 2 — explicit `register` field (only if phase 1 is insufficient)

If confidence+state don't capture the hot-take distinction well enough:

- Add an optional `register: Literal["formal","casual"] | None` (or
  `stance`/`commitment`) to `CandidateMemoryModel` and a nullable
  `register` column to the `Memory` model. This *is* a migration
  (`storage/migrations/versions/`), so only do it if justified.
- Extractor sets `register="casual"` for 1:1 hot takes.
- `query` retrieval downranks/excludes `casual` unless explicitly asked.
- Keep it orthogonal to `memory_type` — a `casual` `opinion` and a `casual`
  `fact` are both valid.

Prefer NOT doing phase 2 until phase 1 is shown to be inadequate. Two new
concepts (a type + a field) at once is exactly the over-engineering
`CLAUDE.md` warns against.

## Acceptance criteria

- [ ] `opinion` is in the extractor's type vocabulary (schema Field description
      + prompt + transcript SKILL.md).
- [ ] Extractor assigns `opinion` to value judgments/interpretations rather
      than `fact`.
- [ ] Casually-asserted 1:1 claims get low `confidence` (≤ ~0.4).
- [ ] `wakil memory list --type opinion` returns them.
- [ ] No DB migration in phase 1.
- [ ] Tests cover the new type + low-confidence path.
- [ ] Docs/README memory-lifecycle section mention `opinion` and how hot takes
      are modeled (confidence + archive), so behavior stays documented per the
      working agreement.

## Reference: manual reclassification already applied to the live KB

For continuity — these rows were retyped by hand before this change existed, so
the extractor should reproduce these outcomes going forward:

- memory #54 ("~$630K comp would be reasonable"): `fact` → `opinion`.
- memory #67 ("paused hiring *because*…"): `fact` → `opinion`.
- memory #66 ("PR volume from ~30/week to N", casual 1:1 metric):
  `fact` → `opinion`, and `state` → `archived` (the hot-take treatment:
  searchable, downranked, never auto-promoted).
