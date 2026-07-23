---
title: Add relationship/graph-traversal queries (backfills ADR 0006 too)
status: proposal
audience: wakil coding agent
---

# Add relationship/graph-traversal queries to `wakil query`

## Problem

Two related gaps surfaced while building a kb-side skill (`wakil-query`)
that shells out to `wakil search`/`wakil query`:

1. **ADR 0006's backlinks were never actually wired up.** ADR 0006 ("Backlinks
   as a Live Query Over a Widened Relationship Table") designed
   `Relationship.subject_note_id`/`object_note_id` for exactly this, and the
   schema/migration exist. But grepping the real application code
   (`src/wakil/app/*.py`) turns up exactly one place a `Relationship` row is
   ever constructed — `ingest_service.py`'s `apply_enrichment`, and it's
   `subject_memory_id`/`object_memory_id` only. The note-note columns are
   exercised solely by `tests/unit/test_migrations.py::test_note_to_note_relationship_roundtrip`,
   a hand-constructed unit test. **In the real, current system, no backlink
   is ever automatic — for wakil-authored content or hand-edited content.**
   Every downstream doc/skill that assumed otherwise (this kb's `CLAUDE.md`,
   `wakil-query`, `enrich`, `quickcapture-ingest`) has been corrected to say
   so plainly (2026-07-23) pending this work.
2. **No relationship/graph-traversal at all.** Even once backlinks are real,
   they'd only be generic "these two notes are linked" edges. There's no way
   to ask a typed, structural question — "who works at Acme?", "who has
   Alice met (via meetings)?" — the way the retired gbrain system's
   `graph-query --type works_at --direction in` could.

## Two build in one pass, deliberately

Phase 1 below is not new scope creep bolted onto a graph-traversal feature —
it's finishing what ADR 0006 already decided and never landed. Phase 2 is
the actual new capability. Doing both together is smaller than it looks,
since Phase 2's traversal only becomes meaningful once Phase 1 populates
real edges.

## Design decision: wikilink extraction belongs in *indexing*, not enrichment

The obvious first instinct is to populate `Relationship` rows during
`wakil enrich`'s own entity-resolution step, alongside the existing
`subject_memory_id`/`object_memory_id` writes. **Don't** — that would only
ever cover content that went through `wakil ingest`/`enrich`, permanently
reproducing the exact "hand-edits don't get backlinks" gap this kb's
`CLAUDE.md` currently documents as an accepted limitation. It doesn't have
to be permanent.

Instead: extend `index_notes()` (`src/wakil/app/workspace_service.py`) — the
same deterministic, no-model-call pass that already syncs the `Note` table
with what's on disk on every `wakil index`/`wakil init` — to also parse each
note body for `[[wikilink]]` targets and upsert generic `Relationship` rows
for them. Consequences of this choice:

- **Every indexed note gets link extraction, hand-edited or wakil-authored,
  old or new**, the next time anyone runs `wakil index`. No separate
  backfill command needed for the existing 2,700+ note corpus — a plain
  reindex covers it.
- The predicate at index time must be something derivable **without a model
  call**, since indexing is cheap and runs constantly — a single generic
  predicate (`mentions`, matching the existing free-string convention on
  `Relationship.predicate`) is all indexing can responsibly claim. This
  is enough to make backlinks (ADR 0006's original goal) actually real.
- Typed, semantic predicates (`works_at`, `founded`, `advises`, `attended`)
  are a **separate, optional** enhancement — Phase 2 below — layered on top
  once generic edges exist, not a blocker for shipping Phase 1.

### Phase 1 — generic Note↔Note edges at index time

1. In `index_notes()`, after upserting/updating a `Note` row, parse its
   current body for `[[path]]` / `[[path|display]]` wikilinks (reuse the
   existing wikilink-form logic already in `ingest_service.py` —
   `_reconcile_note_links`'s neighborhood already parses this shape for
   note-conformance corrections; don't re-derive the regex).
2. Resolve each wikilink target to an existing `Note` row by path (both
   `[[people/x]]` and `[[sources/y.md]]` forms appear for real in this kb,
   per `ingest_service.py`'s own comment — resolve both). A target that
   doesn't resolve to a real note is not an error here — dead-link
   detection is `maintain`'s job, not indexing's; skip silently.
3. Upsert a `Relationship(subject_note_id=<this note>, predicate="mentions",
   object_note_id=<target note>, workspace_id=...)` row — idempotent (dedupe
   on subject/predicate/object so re-indexing an unchanged note is a no-op,
   matching `index_notes()`'s existing unchanged/updated/added counting).
4. On re-index, if a note's wikilinks changed (some removed), remove the
   `Relationship` rows for targets no longer mentioned — mirrors
   `index_notes()`'s existing prune semantics for removed notes.
5. No migration needed — the columns already exist (migration `0002`).

### Phase 2 — typed entity relationships + query surface

1. Extend `wakil enrich`'s entity-resolution output (optionally, only when
   it already resolves two entities together in the same source) with a
   proposed relationship type from a small, free-string-not-fixed-enum
   vocabulary (matching the existing `CandidateRelationshipModel.predicate`
   convention of a described-not-enforced set: `works_at | founded | advises
   | attended | invested_in | mentions`). This reuses an existing model call
   (no new LLM cost) and only covers content going through `wakil enrich` —
   fine, since Phase 1's generic `mentions` edges already give full-corpus
   coverage as a fallback.
2. Add a query surface — a new `wakil relationships <note-path>` command
   (or a `--related`/`--graph` mode on `wakil query`, naming TBD): given a
   note, list linked notes filtered by `--predicate <type>` and
   `--direction in|out|both`, with `--depth N` for multi-hop traversal via a
   SQLite `WITH RECURSIVE` CTE over `Relationship` scoped to note ids — no
   new graph library or database, matching the project's existing bias
   (`CLAUDE.md`: "avoid premature graph databases") since this reuses the
   table ADR 0006 already widened rather than introducing new
   infrastructure.
3. `wakil-query` (the kb-side skill) gets updated once this ships to
   document the new command and retire its current "real gap" disclaimer.

## Open questions

- **Predicate vocabulary**: free string (matches `CandidateRelationshipModel`'s
  existing convention, simplest) vs. a small fixed set enforced at the
  schema layer (more consistent, more upfront work; `Relationship.predicate`
  today is `String(50)`, unconstrained either way). Lean free string,
  consistent with how `memory_type`/`stance` were handled (ADR 0013/0014) —
  don't add enforcement until it's shown to be needed.
- **Depth bound for traversal**: cap the recursive CTE at a fixed max depth
  (e.g. 5, matching gbrain's old default) to avoid runaway queries on a
  densely-linked kb — needs picking, not derived from anything in wakil today.
- **Should Phase 2's typed-relationship extraction be its own model call**
  (more accurate, an added cost) **or folded into existing entity-resolution
  output** (free, but entity-resolution's current job is narrowly "does this
  refer to an existing page," not "what's the relationship type" — scope
  creep on that skill's stated boundary, see `entity-resolution/SKILL.md`).
  Lean toward folding in only if it doesn't blur that skill's contract;
  otherwise a dedicated pass, deferred until Phase 1 alone is shown
  insufficient — mirrors the phase-1-before-phase-2 discipline ADR 0013/0014
  already used for the memory-register work.
- **Command name/shape** for the query surface — `wakil relationships`,
  or a mode on `wakil query` — no strong opinion yet, pick whichever reads
  better once Phase 1 exists to test it against real data.

## Acceptance criteria (Phase 1 only — Phase 2 is a separate follow-up)

- [ ] `index_notes()` extracts `[[wikilinks]]` into generic `mentions`
      `Relationship` rows, idempotently, on every `wakil index`/`init`.
- [ ] Re-indexing an unchanged note doesn't duplicate or churn rows.
- [ ] Removing a wikilink from a note removes the corresponding row on
      next reindex.
- [ ] Both `[[people/x]]` and `[[sources/y.md]]` wikilink forms resolve.
- [ ] A wikilink to a nonexistent note is skipped, not an error.
- [ ] Tests cover: fresh index populates edges; unchanged reindex is a
      no-op; removed link prunes its row; both wikilink forms resolve;
      a dead-link target is skipped cleanly.
- [ ] This kb's `CLAUDE.md`/`wakil-query`/`enrich`/`quickcapture-ingest`
      corrections (2026-07-23, "not wired up yet") get a follow-up update
      once Phase 1 ships, since the gap they document will be closed.
