---
title: Backlinks as a Live Query Over a Widened Relationship Table
status: accepted
date: 2026-07-10
audience: wakil design
---

# Backlinks as a Live Query Over a Widened Relationship Table

## Context

`wakil`'s target vault convention (GBrain/Obsidian-style entity pages) bakes
backlinks into Timeline prose, e.g. `- **2026-06-04** | Referenced in
['2024 06 12 Wed'](journal/...)`. This is derived graph metadata, not
evidence of what happened, and it directly contradicts the vault's own
definition of Timeline as an "append-only evidence log": the file churns
every time something merely *references* it, and mechanical noise
interleaves with real chronological content.

`wakil` already has a `Relationship` table (`storage/schema.py`), but it
only models Memory↔Memory semantic edges (`subject_memory_id`,
`predicate`, `object_memory_id`). Wikilinks are a different kind of edge —
Note↔Note structural links — with no existing home. Options considered:

- **A new `links` table** (GBrain's approach): a dedicated graph table
  mirroring `Relationship`'s shape but scoped to notes. Rejected — it
  forks the same subject/predicate/object concept into a second,
  parallel table for no benefit over widening the one that already
  exists.
- **Storing backlinks as prose** (status quo in the vault): rejected
  outright — this is the anti-pattern being fixed, not an option.
- **Widen `Relationship` with nullable `subject_note_id` /
  `object_note_id` columns**, mirroring the nullable-provenance pattern
  the table already uses for `source_id`/`note_id`. Chosen.

## Decision

Backlinks are a live SQL query over the existing `Relationship` table,
never stored prose and never a new table. `Relationship` is widened with
two new nullable columns, `subject_note_id` and `object_note_id`, so it
can carry Note↔Note structural edges (wikilinks) alongside its existing
Memory↔Memory semantic edges. A backlink for note X is computed as
`SELECT ... FROM relationships WHERE object_note_id = X` at read time —
there is no stored "referenced in" text anywhere, in SQLite or in
Markdown. `wakil`'s writer never emits backlink lines into Timeline; its
indexer recognizes legacy backlink lines (`^- .*\| Referenced in`) as
noise and offers their removal as a reviewable diff during synthesis,
never a silent rewrite.

## Consequences

- Fixes the root cause, not the symptom: banning backlink text in the
  writer alone would still leave no way to answer "what links to this
  note" without re-introducing stored prose; the widened `Relationship`
  table makes backlinks queryable without writing anything to disk.
- One schema change (two nullable columns) instead of a second graph
  table, keeping the Memory and Note edge concepts in one place with one
  lifecycle to maintain.
- Existing rows are unaffected: `subject_note_id`/`object_note_id` are
  nullable, so today's Memory↔Memory relationships are untouched and the
  same table now serves two edge kinds distinguished by which pair of
  columns is populated.
- Populating these columns from actual wikilink/tag extraction during
  indexing is separate follow-up work, tracked as a TODO item, not part
  of this decision.
- Migrating a vault off baked-in backlink text requires a one-time
  reviewable cleanup pass (indexer flags legacy lines, human confirms
  removal) rather than an automatic rewrite, consistent with `wakil`'s
  no-silent-rewrite principle.

## Sources

- `docs/entity-model.md`: "Existing `Relationship` table, widened with two
  new nullable columns, `subject_note_id` and `object_note_id`... Backlinks
  become a live query (`WHERE object_note_id = X`) — the actual fix for the
  baked-in-text anti-pattern above, not just a ban on the symptom."
- `src/wakil/storage/schema.py` (`Relationship` model): implements
  `subject_note_id` / `object_note_id` as nullable `ForeignKey("notes.id")`
  columns with the same rationale in an inline comment.
- Session transcript:
  `~/.claude/projects/-Users-ebridges-Projects-wakil/cf777bf3-daa2-4c94-a23b-f7f23c917f52.jsonl`
