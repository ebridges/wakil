---
title: Prioritize @-referenced notes in related-notes search, thread resolve_context past the CLI layer
status: accepted
date: 2026-07-21
audience: wakil design
---

## Context

`context_references.resolve_context()` (added to support `@file:path`/`@url:url` expansion inside
`--context`/`--context-file`, see PR #18) was deliberately kept CLI-layer-only: it returned a plain
`str | None`, and `prepare_capture`/`prepare_enrichment` in `ingest_service.py` kept their existing
signatures unchanged, taking only the already-resolved `context: str | None`. That boundary meant a
note pulled in via `@file:` had no privileged status once resolution finished — it became part of an
undifferentiated context string, indistinguishable from prose the user typed by hand.

Two problems followed from collapsing everything into one string:

1. **No guaranteed backlink.** `prepare_enrichment`'s `related_query` folds `context` in wholesale
   and hands the result to `search_workspace` (QMD/FTS), capped at `RELATED_NOTE_LIMIT` (5) hits. A
   file the user explicitly named with `@file:` — the strongest possible relevance signal available —
   only shows up in "Existing related notes:" (what the extraction/entity-resolution prompts render
   and are instructed to wikilink) if it happens to also win a keyword-ranking contest. An explicit
   reference should never be optional in this way.
2. **Search precision.** An attached file's full text (easily 1000+ tokens) got folded into
   `related_query` and into `_candidate_entity_notes`'s input raw, in both cases uncapped. This
   diluted the SQLite FTS5 OR-query and QMD BM25 ranking into an unfocused bag of words, and it let
   `_candidate_entity_notes`'s `_PROPER_NOUN_RE` regex spuriously match the literal string
   `"Attached Context"` (two capitalized words) as a fake entity-name candidate.

Fixing both required `prepare_capture`/`prepare_enrichment` to see more than the final joined string:
which paths were successfully referenced, and a version of the context text with attachment dumps
excluded for building search queries. That information already existed inside
`context_references.expand_piece`/`resolve_context` — it was being computed and then thrown away.

## Decision

Reverse the earlier boundary decision:

- `resolve_context()` now returns a `ResolvedContext` dataclass (`text`, `digest`,
  `referenced_paths`) instead of a bare string — `digest` is the assembled context with
  `--- Attached Context ---` blocks excluded, built from the same pre-attachment pieces
  `expand_piece` already had in hand, not by regex-stripping the merged string after the fact.
  `referenced_paths` are the KB-relative paths of every `@file:` reference that resolved.
- `prepare_capture`/`prepare_enrichment` signatures grow two new optional keyword parameters,
  `context_digest` and `context_referenced_paths`, threaded from `main.py`'s
  `_resolve_context_or_exit`.
- `prepare_capture`/`apply_capture` persist `context_digest`/`context_referenced_paths` into
  `Source.metadata_json`, alongside the existing `context`/`meeting_date` keys, using the same
  schemaless-blob pattern. `prepare_enrichment` reads them back with the same
  `x = x or metadata.get("x")` fallback already used for `context`, so re-running
  `wakil enrich <id>` without repeating `--context` still benefits.
- `related_query` and `_candidate_entity_notes`'s input are built from `context_digest` (falling
  back to raw `context` for sources captured before this change) instead of the raw context string.
- `context_referenced_paths` become guaranteed entries in `related_notes` — not subject to
  `RELATED_NOTE_LIMIT`, listed ahead of search-derived hits, tagged with a distinct
  `SearchHit.engine` value (`"user-referenced"`) — deduped against `search_workspace` hits and the
  source's own raw-text path exactly like the existing `entity-name` supplement already is.

This is a direct reversal of the "resolve_context stays CLI-layer-only" call made when
`@file:`/`@url:` expansion first landed: the CLI/service boundary is being pushed back in favor of
giving `ingest_service.py` the structured information it needs to treat an explicit reference as a
guarantee rather than a hint.

## Consequences

- A note reached via `@file:` is now always wikilinked as a candidate — extraction and
  entity-resolution prompts render it in "Existing related notes:" every time, regardless of
  QMD/FTS ranking.
- Related-notes search queries stay tight (title + digest + a slice of the source text) instead of
  ballooning with attached-file content, and `_candidate_entity_notes` no longer treats
  `"Attached Context"` as a spurious proper-noun candidate.
- `prepare_capture`'s signature changed to accept `context_digest`/`context_referenced_paths`. This
  is expected to conflict on merge with a separate in-flight PR ("capture-time title/abstract
  generation") that also changes `prepare_capture`'s signature (adding a required `ModelClient`
  parameter) — flagged in the implementing PR for manual reconciliation, not avoided here.
- Sources captured before this change have no `context_digest`/`context_referenced_paths` in their
  metadata; `prepare_enrichment` falls back to the raw `context` string for query-building, exactly
  as before this change, so old sources degrade gracefully rather than erroring.

## Implementation

- **PR #19** — "Prioritize @-referenced notes in related-notes search, fix query bloat"

## Sources

- `src/wakil/app/context_references.py` (`resolve_context`, `expand_piece`, `ResolvedContext`)
- `src/wakil/app/ingest_service.py` (`prepare_capture`, `apply_capture`, `prepare_enrichment`,
  `_candidate_entity_notes`)
- `src/wakil/cli/main.py` (`_resolve_context_or_exit`)
- `src/wakil/storage/fts.py` (`to_match_expression`)
- PR #18 — "Repeatable --context/--context-file with @file:/@url: expansion" (the original
  CLI-layer-only decision this ADR reverses)
