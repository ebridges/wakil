---
title: QMD as First-Class Search Over SQLite FTS5
status: accepted
date: 2026-07-09
audience: wakil design
---

# QMD as First-Class Search Over SQLite FTS5

## Context

Phase 2 of `wakil`'s build plan (PR #2, "feat(phase-2): search and query —
FTS5, QMD wrapper, model client, cited answers", merged 2026-07-09) needed to
give `wakil search` and `wakil query` something to retrieve against. Two
retrieval mechanisms landed in the same PR:

- **QMD** ([tobi/qmd](https://github.com/tobi/qmd)), an external CLI that
  indexes the Markdown files of the knowledge base directly and supports BM25
  keyword search (`search`), vector similarity (`vsearch`), and hybrid
  LLM-reranked search (`query`).
- **SQLite FTS5**, external-content tables over `wakil`'s own operational
  records — notes, memories, and sources — kept in sync via triggers on the
  existing SQLAlchemy ORM writes.

The module docstring added in this PR (`src/wakil/integrations/qmd.py`)
states the decision plainly:

> "QMD (https://github.com/tobi/qmd) is the first-class search engine for the
> knowledge base."

and `search_service.search_workspace` encodes the resulting precedence
directly in its docstring:

> "QMD results first (knowledge base is the source of truth), then FTS."

The shipped README section ("## Search") describes the same behavior: QMD
runs when the `qmd` binary is installed and takes precedence; FTS5 "fills in
workspace records QMD doesn't cover" (memories and sources, which are
database rows, not Markdown files QMD can index).

I did not find any document, PR description, or transcript that argues QMD
against FTS5 as competing alternatives for the same content — see
Consequences below for what the record does and doesn't establish.

## Decision

`wakil` treats QMD as the first-class search engine over the Markdown
knowledge base itself, and SQLite FTS5 as the mechanism for the records that
only exist inside `wakil`'s own database (memories, sources, and note
metadata):

- `wakil search` queries QMD first (when the `qmd` binary is present) and
  merges in FTS5 hits for anything QMD doesn't cover, de-duplicating by note
  path (`search_service.search_workspace`, `src/wakil/app/search_service.py`).
- FTS5 is implemented as external-content tables over `notes`, `memories`,
  and `sources`, synced via triggers so ordinary ORM writes update the index
  with no extra application code (PR #2 summary).
- If `qmd` is not installed, `qmd_search` degrades gracefully to an empty
  result list rather than failing (`src/wakil/integrations/qmd.py`).
- The design was later reinforced in PR #14 ("Entity-revision DAG, schema
  page-shapes, and QMD workspace collections", merged 2026-07-18), which
  scoped QMD's own index/collections to `<workspace>/.wakil/qmd/` — a sibling
  file to `wakil.db`, not merged into it — because `qmd` manages its own
  SQLite schema via an independent process with no locking coordination with
  `wakil`'s SQLAlchemy connection.

## Consequences

- Search quality for note content depends on an external binary (`qmd`)
  being installed and correctly invoked; when it is absent, `wakil` silently
  falls back to FTS5-only results over indexed note metadata (title,
  frontmatter, content hash — not full note bodies, per PR #1's indexing
  scope).
- This dependency is not free of risk in practice: a later troubleshooting
  session found `qmd_search()`'s subprocess invocation passing CLI flags
  that don't exist in the installed `qmd` (v2.1.0), causing search to return
  zero results unconditionally for any query (transcript,
  `09cc95ef-515c-4a06-b5c9-92c68f3679cf.jsonl`, 2026-07-16). A separate
  session found QMD's relevance scoring returning a flat, non-discriminating
  score (e.g. `0.88`) on every result for a given query, confirmed against
  the raw `qmd` CLI rather than a `wakil`-side parsing bug (transcript,
  `93aecd7b-3db1-4708-aaf6-81eeb2653a37.jsonl`, 2026-07-17). Both indicate
  the QMD integration is more fragile in practice than "first-class" status
  implies, and that its behavior needs to be re-verified whenever the `qmd`
  CLI's flags or scoring semantics change.
- Keeping QMD's index as a sibling file under `.wakil/qmd/` rather than
  inside `wakil.db` avoids lock contention between `qmd`'s independent
  process and `wakil`'s SQLAlchemy connection, at the cost of two on-disk
  index stores per workspace instead of one.
- **Honesty about what the record does not establish:** the merged PR and
  its docstrings assert QMD's first-class status and describe the resulting
  precedence order, but I found no comparative rationale in the PR
  description, commit messages, README, or TODO.md explaining *why* QMD was
  chosen as the primary engine over extending FTS5 to cover full note bodies
  (e.g., ranking quality, semantic/vector search support, maintenance cost,
  or alignment with "Markdown as source of truth"). The closest thing to a
  rationale is the design-biases language in `CLAUDE.md` ("QMD as
  first-class search" is listed directly as a project-level bias, alongside
  "SQLite FTS5 for internal records") and the search-service comment
  "knowledge base is the source of truth" — but neither is a documented
  decision that weighs QMD against FTS5 as alternatives for the same job. If
  that comparative reasoning exists, it predates this PR and wasn't captured
  in the repo's history or the transcripts reviewed for this ADR.

## Sources

- PR #2, "✨ feat(phase-2): search and query — FTS5, QMD wrapper, model
  client, cited answers" (merged 2026-07-09, merge commit
  `6231086767aaf2e68a2b80ed4dd815bb4e45250d`) — PR description: "QMD results
  first (Markdown is the source of truth), local FTS filling in
  notes/memories/sources QMD doesn't cover."
- `src/wakil/integrations/qmd.py` (as of PR #2) — module docstring: "QMD
  (https://github.com/tobi/qmd) is the first-class search engine for the
  knowledge base."
- `src/wakil/app/search_service.py`, `search_workspace()` docstring: "QMD
  results first (knowledge base is the source of truth), then FTS."
- README.md (as of PR #2), section "## Search": "QMD results take
  precedence; FTS fills in workspace records QMD doesn't cover."
- TODO.md (as of PR #2), "## Phase 2: Search and Query" checklist.
- PR #14, "Entity-revision DAG, schema page-shapes, and QMD workspace
  collections" (merged 2026-07-18) — moved QMD's index to
  `<workspace>/.wakil/qmd/` as a sibling of `wakil.db`.
- `src/wakil/config/settings.py`, `WorkspaceConfig.qmd_dir` docstring:
  "separate file from wakil.db — qmd manages its own SQLite schema via an
  independent process with no locking coordination with wakil's connection."
- Transcript `~/.claude/projects/-Users-ebridges-Projects-wakil/6f77ae00-a4f5-49eb-b09b-3fa836b62e4c.jsonl`
  (approx. 2026-07-18T07:22:47Z): "QMD's own index and collection config are
  scoped to each wakil workspace via the `QMD_CONFIG_DIR`/`INDEX_PATH`
  environment variables it already respects, pointed at
  `<workspace>/.wakil/qmd/` — a sibling of `wakil.db`, not the same physical
  file (qmd manages its own SQLite schema via an independent process with no
  locking coordination with wakil's SQLAlchemy connection)."
- Transcript `~/.claude/projects/-Users-ebridges-Projects-wakil/09cc95ef-515c-4a06-b5c9-92c68f3679cf.jsonl`
  (approx. 2026-07-16T19:28:11Z): "Confirmed: `wakil`'s QMD integration
  returns zero results unconditionally, for any query. The root cause is in
  `qmd_search()`'s subprocess invocation — it passes CLI flags that don't
  exist in the installed `qmd` (v2.1.0)."
- Transcript `~/.claude/projects/-Users-ebridges-Projects-wakil/93aecd7b-3db1-4708-aaf6-81eeb2653a37.jsonl`
  (approx. 2026-07-17T14:31:45Z): "QMD returned a flat `\"score\": 0.88` on
  *every* result for a \"Mosaic\" query — confirmed by running the raw `qmd`
  CLI directly, not a wakil parsing bug. QMD isn't grading relevance at all
  in this mode."
- `CLAUDE.md`, "Design Biases" section: lists "QMD as first-class search"
  and "SQLite FTS5 for internal records" as project-level preferences (no
  comparative rationale given there either).
