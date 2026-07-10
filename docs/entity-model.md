---
title: Modeling Entity Documents (Compiled Truth / Timeline) in wakil
status: draft
audience: wakil design
---

# Modeling Entity Documents in wakil

`wakil`'s target knowledge base uses a two-part convention for important
entity pages — people, companies — inherited from GBrain and documented in
the target vault's own `schema.md`: a **Compiled Truth** section holding
synthesized current state, and a **Timeline / Log** holding an append-only,
reverse-chronological evidence record. `wakil/PROMPT.md` already commits to
preserving this split, but nothing in the codebase implements it yet:
`Note`/`Memory`/`Relationship` exist in `storage/schema.py`, but there is no
notion of a Timeline entry, no Open Threads lifecycle, and no command that
touches a Compiled Truth section.

This spec starts from a critical read of how the pattern is actually used in
a real, long-running vault, then proposes a concrete model — what belongs in
the Markdown, what belongs in SQLite, and how content moves between them —
that fills the gap without importing the large autonomous system GBrain uses
to back the same visible format.

## What the pattern looks like in practice

Sampled directly from a real vault's `companies/` and `people/` documents,
against the documented spec in that vault's `schema.md` §2 ("The Page
Standard: compiled truth + timeline").

1. **Compiled Truth is empty for most of the vault.** The large majority of
   sampled pages carry only the placeholder text (`_Synthesized current
   state — rewrite when facts change._`) with zero real content, and well
   over a hundred lines of raw, unsynthesized interview notes dumped into
   Timeline instead. The synthesis step the format promises has not run for
   most entities — the two-part structure is aspirational, not actual, for
   most of the vault.

2. **Backlinks are baked into Timeline text**, e.g. `- **2026-06-04** |
   Referenced in ['2024 06 12 Wed'](journal/...)`. These are derived graph
   metadata, not evidence of what happened, and they directly contradict the
   vault's own definition of Timeline as an "append-only evidence log." This
   causes two concrete problems: the file changes every time something
   merely *references* it — churn unrelated to the entity's own history —
   and it interleaves mechanical noise with real chronological content,
   sometimes literally ahead of the real entries in file order.

3. **Open Threads drifted from its documented shape.** The vault's own
   `schema.md` defines Open Threads as an inline bullet inside Compiled
   Truth (`- **Open threads**: what is unresolved`). Real files that use it
   instead give it its own `## Open Threads` H2. The convention as practiced
   differs from the convention as written, with nothing flagging the drift.

4. **Provenance is carried as hand-written prose tags** —
   `[Source: [[sources/...]], 2026-06-23]` — with no structured backing. It
   works as long as a human maintains it carefully, but it's unqueryable
   ("what do we know about X sourced only from meeting Y?") and easy to lose
   silently on a hand edit, since nothing checks that a claim's citation
   survives a rewrite.

5. **The simplicity of the visible format hides a large system.** In GBrain
   (inspected via its code graph), Compiled Truth and Timeline are thin views
   over a `pages` table (`compiled_truth`, `timeline` TEXT columns, plus a
   generation counter for cache invalidation), a `page_versions` snapshot
   table, a structured `timeline_entries` table
   (`page_id, date, source, summary, detail`), a `links` graph table,
   `content_chunks` with synthesis-boosted search ranking, silent
   `stubEntityPage` auto-creation for referenced-but-undocumented entities,
   and a fact/atom extraction pipeline with contradiction detection feeding
   synthesis. That is a large, partly-autonomous system — exactly what
   `wakil/CLAUDE.md` rules out ("large agent frameworks," "hidden background
   behavior," "automatic rewriting without review," "opaque memory
   systems"). The markdown pattern is worth keeping; the machinery GBrain
   uses to back it is not something to port wholesale.

**Bottom line:** the format is sound and worth preserving, but a real vault
run this way for a while shows what happens when the promotion step (raw →
synthesized) has no tooling: content piles up in the append-only half and the
"what's true now" half rots. That's the gap `wakil` needs to fill —
deliberately, with human confirmation at the point of synthesis, not by
copying GBrain's autonomous pipeline.

## Markdown shape (source of truth, unchanged in spirit)

```markdown
# Display Name

## Compiled Truth
<synthesized, always-current summary>
- state-field bullets

## Open Threads
- unresolved items, each citing its source

---

## Timeline / Log
### 2026-04-16 — [Source: [[sources/...|label]]]
- what happened
```

- **Open Threads gets its own H2** — codifying what real files already do in
  practice, and giving `wakil` a stable H2→H2 parse boundary instead of
  scraping mixed-purpose bullets out of prose.
- **No backlink text in Timeline, ever.** `wakil`'s writer never emits
  "Referenced in" lines; backlinks are a query, not stored prose (see
  below). `wakil`'s indexer recognizes legacy backlink lines as noise
  (`^- .*\| Referenced in`) and offers their removal as part of a reviewable
  diff during synthesis — never stripped silently.
- Timeline citations standardize on `[Source: [[wikilink|label]]]` — already
  the pattern in the better-written pages — over a bare `source: fathom`
  label, since a wikilink is resolvable and a plain label isn't.
- No new frontmatter marker is needed to identify an entity page:
  `type: person|company|...` already says so, and `Note` indexing already
  captures frontmatter.

Because the target vault is external to `wakil`, this shape is a proposed
amendment to that vault's own `schema.md`, reviewed and merged there like any
other change — not a `wakil`-only convention enforced silently.

## What lives in SQLite, and why

`wakil` extends its existing `Note`/`Memory`/`Relationship` tables rather
than adding a parallel schema. Each choice below is checked against the
project's own bar: *does this clearly improve local knowledge work for one
user?*

| Data | Verdict |
|---|---|
| **Compiled Truth content** | Not stored in SQLite. Markdown only — git already provides full version/diff/revert history, which is exactly what a GBrain-style `page_versions` table would duplicate. |
| **Open Threads items** | Existing `Memory` rows, `memory_type='question'` (already named as an example Memory type), `note_id` = the entity's `Note`. Resolution is a `state` transition plus a `Relationship(predicate='resolves')` edge — the "resolved open-threads migrate down into the timeline" rule, with zero new schema. |
| **Timeline entries** | Existing `Memory` rows, `memory_type='event'` (a new value on the already free-string column — no migration), `note_id` = entity `Note`, `source_id` = originating `Source`. **One genuine new column: `Memory.event_date` (nullable date).** `created_at` records when the SQLite row was written, not when the dated event happened, and Timeline ordering needs the latter. |
| **A dedicated `timeline_entries` table (GBrain-style)** | Rejected. Its only advantage over `SELECT * FROM memories WHERE note_id=X AND memory_type='event' ORDER BY event_date` is a marginally cleaner query, at the cost of forking the whole Memory lifecycle (state, promotion, dedup) into a second, unmaintained path. |
| **Backlinks / graph edges** | Existing `Relationship` table, widened with two new nullable columns, `subject_note_id` and `object_note_id` (mirrors the nullable-provenance pattern the table already uses for `source_id`/`note_id`). Wikilinks are Note↔Note structural edges, not Memory↔Memory semantic ones — today's `Relationship` only models the latter. Backlinks become a live query (`WHERE object_note_id = X`) — the actual fix for the baked-in-text anti-pattern above, not just a ban on the symptom. |
| **Search-ranking boost for synthesized content (GBrain's `chunk_source`)** | Not now — solves a chunked/hybrid-search problem `wakil` doesn't have yet; revisit only if a chunking/embedding layer lands. |

## Write / promotion path — no autonomous rewriting anywhere

Every step is propose → diff → confirm, reusing the two-phase
`prepare_ingest` / `apply_ingest` plus `IngestProposal` / `ProposedFile`
shape already implemented in `src/wakil/app/ingest_service.py`.

1. **Ingest** (implemented today): raw source → candidate `Memory` rows.
   Extraction is extended to tag date-bearing, entity-linked content as
   `memory_type='event'` with `event_date` set and `note_id` resolved
   through the existing related-note search / resolver routing.
2. **Review/promote** (Phase 5, not yet built): `wakil memory promote <id>`
   moves a memory to durable — no special-casing for `event`/`question`
   types, same command, same review queue.
3. **Timeline append**: once a durable `event` memory has a `note_id`,
   `wakil` proposes appending a `### <event_date> — [Source: ...]` block to
   that note's Timeline as an explicit confirmable step during promotion
   ("append to `<slug>` Timeline? [y/N]") — never folded in silently.
4. **Compiled Truth synthesis — new command `wakil entity compile <slug>`**:
   - Resolve `<slug>` → `Note`, verify frontmatter `type` is an entity type.
   - Pull all durable `Memory` rows tied to that `note_id`.
   - The model drafts a rewritten Compiled Truth + Open Threads block
     only — Timeline is never touched by this command.
   - Render as a colorized diff of just that section (a surgical replace,
     not a whole-file overwrite), reusing the existing diff-preview UI.
   - On confirm: write, commit as `wakil note: recompile <slug> compiled
     truth`, logged via `GitChange` (`operation="entity_compile"`) rather
     than `IngestRun`, which is source-shaped and doesn't fit a
     no-source operation.
   - The same pass proposes moving any now-resolved Open Thread down into a
     one-line Timeline entry, shown in the same diff.
   - This is the concrete, entity-scoped version of `docs/memory-model.md`'s
     abstract "reconsolidation as a reviewable proposal."

## Entity auto-stubbing

`wakil` does not replicate GBrain's silent `stubEntityPage`. New-file
creation is exactly the durable-Markdown change that must stay reviewable.
Instead, the existing ingest preview is extended: when relationship
extraction finds a mentioned entity with no matching `Note`, it's added to
the `IngestProposal` as `stub_entities: list[ProposedFile]`, pre-filled via
the same resolver-driven routing already used for proposed notes, shown in
the same diff, and created only on confirmation. For ingests that mention
many people (e.g. a transcript), the default is one summary confirmation
("create N stub pages? [y/N]") rather than N separate diffs.

## Deviations from existing docs — flagged, not silent

- **Memory state vocabulary**: this model is written against `PROMPT.md`'s
  implemented states (`working/candidate/durable/archived/rejected`,
  matching `storage/schema.py`), not `docs/memory-model.md`'s proposed
  `candidate/active/dormant/archived` plus `strength/activation/
  support_count` model. The Timeline/Open-Threads-as-Memory mapping is
  orthogonal to which vocabulary wins; reconciling the two is a separate,
  still-open decision.
- **New `memory_type='event'`** — not in `PROMPT.md`'s example list, but
  backward-compatible since the column is a free string.
- **New `Memory.event_date` column** — a genuine addition beyond
  `PROMPT.md`'s field list; justified above.
- **New `Relationship.subject_note_id` / `object_note_id`** — a genuine
  addition; today's `Relationship` is Memory-only.
- **Explicitly rejected from GBrain**: `page_versions` (redundant with git),
  `timeline_entries` (redundant with `Memory`), `links` (redundant with a
  widened `Relationship`), `chunk_source` boosting (no chunking layer yet),
  silent `stubEntityPage` auto-creation (violates confirm-before-write), and
  the whole fact/atom/contradiction/dream-cycle autonomous pipeline (out of
  scope, and in conflict with `wakil`'s report-only `dream` command).

## Where this lands in `TODO.md`

This is new design surface. It extends **Phase 5: Memory Lifecycle**
(currently empty) with the entity-specific pieces above, and adds one item
under the existing "Wikilink/tag extraction during indexing" TODO to also
populate `Relationship.subject_note_id` / `object_note_id`.

## Critical files

- `src/wakil/storage/schema.py` — `Memory` / `Relationship` extensions
  (`event_date`, `subject_note_id`, `object_note_id`)
- `src/wakil/app/ingest_service.py` — extend `prepare_ingest` /
  `apply_ingest` and `ProposedFile` / `IngestProposal` for `stub_entities`
  and Timeline-append proposals
- `src/wakil/app/workspace_service.py` — wikilink/backlink indexing
  (existing TODO item)
- new `src/wakil/app/entity_service.py` (or similar) — `entity compile`
  command logic
- target vault's `schema.md` — proposed amendment (Open Threads as H2, no
  backlink text in Timeline, wikilink-style Timeline citations), reviewed as
  a change to that vault, not to `wakil`
