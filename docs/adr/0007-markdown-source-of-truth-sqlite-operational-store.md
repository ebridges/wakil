---
title: Markdown as source of truth, SQLite as operational store
status: accepted
date: 2026-07-10
audience: wakil design
---

## Context

`wakil` needs a durable place to store two different kinds of content: the
knowledge base itself (notes, entity pages, Compiled Truth / Timeline
sections) and the operational data `wakil` needs to search, link, and
review that knowledge base efficiently (frontmatter fields, memory rows,
relationship edges, full-text index).

`docs/entity-model.md`'s "What lives in SQLite, and why" section works
through this concretely for the GBrain-style "Compiled Truth + Timeline"
entity page pattern: for each candidate piece of data it asks whether
storing it in SQLite clearly improves local knowledge work over leaving it
in Markdown, and lands on Markdown-only for Compiled Truth content ("git
already provides full version/diff/revert history, which is exactly what a
GBrain-style `page_versions` table would duplicate") and existing
`Memory`/`Relationship` rows (extended with a few nullable columns) for
everything operational — Open Threads, Timeline entries, backlinks.

`docs/entity-metadata.md`'s "Implications for wakil" section confirms the
same split already holds for frontmatter: `wakil` stores it as an opaque
blob (`Note.frontmatter_json`, `src/wakil/storage/schema.py:70`, parsed via
`python-frontmatter`) rather than modeling each vault type's fields in
SQLite, "the right call given how wide the real drift documented above is —
a strict per-type Pydantic model would reject a large fraction of this
vault's actual files."

## Decision

Markdown files remain the single source of truth for durable knowledge-base
content. SQLite holds only operational metadata needed to search, link, and
manage review workflows over that content — frontmatter as an opaque blob,
`Memory` rows, `Relationship` edges, and the full-text index — never a
parallel copy of durable prose.

## Consequences

- No SQLite table stores rendered/durable note content; Compiled Truth and
  Timeline text live only in Markdown, with git as the version history.
- Frontmatter is not schema-validated per entity type in SQLite; it's
  stored as JSON and parsed on demand, so vault drift from the documented
  schema doesn't break ingestion.
- `docs/entity-model.md`'s "Deviations from existing docs" section
  explicitly rejects porting several GBrain mechanisms as redundant with
  this split: a `page_versions` snapshot table (redundant with git), a
  dedicated `timeline_entries` table (redundant with `Memory`), a `links`
  graph table (redundant with a widened `Relationship`), `chunk_source`
  search-ranking boost (no chunking layer yet), and silent `stubEntityPage`
  auto-creation (violates confirm-before-write).

## Sources

- docs/entity-model.md — "What lives in SQLite, and why" section
- docs/entity-model.md — "Deviations from existing docs — flagged, not
  silent" section
- docs/entity-metadata.md — "Implications for wakil" section
- docs/memory-model.md:18 — "Markdown as the source of truth, SQLite as the
  operational index"
- src/wakil/storage/schema.py:70 — `Note.frontmatter_json`
- transcript cf777bf3-daa2-4c94-a23b-f7f23c917f52.jsonl, line 177,
  2026-07-10T20:46:09.653Z — Write tool call authoring docs/entity-model.md
- transcript 5bea7345-5de7-4124-b605-8ba07bd91721.jsonl, line 175,
  2026-07-11T13:19:49.379Z — Write tool call authoring
  docs/entity-metadata.md (source of the "opaque blob" quote)
