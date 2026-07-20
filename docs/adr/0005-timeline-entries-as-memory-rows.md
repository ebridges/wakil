---
title: Timeline Entries Live in Memory, Not a Dedicated timeline_entries Table
status: accepted
date: 2026-07-10
audience: wakil design
---

# Timeline Entries Live in Memory, Not a Dedicated `timeline_entries` Table

## Context

While modeling entity documents (the Compiled Truth / Timeline convention
inherited from GBrain) for `wakil`, the question arose of how to store
Timeline entries — the append-only, dated evidence log on an entity page —
in SQLite.

GBrain's own implementation (inspected via its code graph) backs this with a
dedicated, structured `timeline_entries` table (`page_id, date, source,
summary, detail`), separate from any generic memory/fact table.

`wakil` already has a `Memory` table with a lifecycle (state transitions,
promotion, dedup) built for exactly this kind of durable, provenance-carrying
record. Introducing a second table for timeline data would fork that
lifecycle rather than reuse it, in a codebase whose stated principle is:
"Keep the implementation simple unless added complexity has a clear and
self-evident impact on the target use case."

## Decision

Reject a dedicated `timeline_entries` table. Timeline entries are existing
`Memory` rows with `memory_type='event'` (a new value on the already
free-string column, requiring no migration), `note_id` set to the entity's
`Note`, and `source_id` set to the originating `Source`. The only genuine
schema addition is `Memory.event_date` (nullable date), needed because
`created_at` records when the SQLite row was written, not when the dated
event actually happened — and Timeline ordering needs the latter.

Fetching an entity's timeline becomes:

```sql
SELECT * FROM memories
WHERE note_id = X AND memory_type = 'event'
ORDER BY event_date
```

## Consequences

- No new table, no new lifecycle to maintain, no new promotion/dedup path to
  keep in sync with the one `Memory` already has.
- Timeline entries go through the same review/promote flow as every other
  Memory (`wakil memory promote <id>`), with no special-casing by type.
- The tradeoff is explicit: a dedicated table would offer a marginally
  cleaner query, but at the cost of maintaining a second, easily-neglected
  path for state, promotion, and dedup that duplicates what `Memory` already
  does.
- This decision is paired with a similar rejection of GBrain's `links` table
  in favor of widening `Relationship`, and of GBrain's `page_versions` table
  in favor of relying on git history — all following the same rule: reuse
  `Note`/`Memory`/`Relationship` rather than add parallel schema, unless the
  addition is a genuinely new column with no existing home.

## Sources

- `docs/entity-model.md` (§"What lives in SQLite, and why"): "A dedicated
  `timeline_entries` table (GBrain-style) | Rejected. Its only advantage over
  `SELECT * FROM memories WHERE note_id=X AND memory_type='event' ORDER BY
  event_date` is a marginally cleaner query, at the cost of forking the whole
  Memory lifecycle (state, promotion, dedup) into a second, unmaintained
  path."
- Session transcript: `/Users/ebridges/.claude/projects/-Users-ebridges-Projects-wakil/cf777bf3-daa2-4c94-a23b-f7f23c917f52.jsonl`
