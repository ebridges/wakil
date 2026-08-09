---
title: Generate title and abstract at capture time with a single model call
status: accepted
date: 2026-07-21
audience: wakil design
---

## Context

`app/ingest_service.py`'s own module docstring, and the two-step ingest design it documents, drew a hard line: capture (`wakil ingest ...`) is deterministic and makes no model call at all; only enrichment (`wakil enrich <source-id>`) touches a model. Capture's title came from mechanical filename massaging (strip a leading date, de-hyphenate) for transcripts/text, or from whatever `<title>` an article's HTML happened to expose for articles. Neither is grounded in what the source actually says.

In practice this produces poor search relevance and thin recall over time: a transcript named `2026-07-16-mosaic-eleni-karahalios.txt` becomes the title "mosaic eleni karahalios" — no indication of what was discussed — and an article's scraped `<title>` is frequently generic, truncated, or SEO boilerplate unrelated to the article's actual content. `wakil search`/`wakil query` (QMD + FTS5, per ADR 0002) rank and surface sources largely by their titles and any indexed text; a source's raw capture is indexed verbatim (`workspace_service.index_notes`), but a one-line mechanical title is a weak signal compared to a few sentences a model can write once it has actually read the text. This gap doesn't close on its own — a source can sit un-enriched for a long time, or never get enriched at all if it doesn't warrant a durable note, so a title/abstract that only improves at enrichment time leaves plenty of sources permanently under-described.

The fix is naturally a capture-time concern, not an enrichment-time one: it needs to run for every source regardless of whether enrichment ever happens, and it needs to be re-runnable independently of the enrichment DAG (docs/adr/0008) so that sources captured before this feature existed can be backfilled without re-triggering extraction/entity-resolution/memory creation.

## Decision

Add one small, cheap, structured model call to the capture step: `_generate_capture_metadata` builds a prompt (`build_capture_metadata_prompt`) from the captured text (plus user-supplied context) and validates the response against a new `CaptureMetadata` contract (`title: str`, `abstract: str`) via the existing `complete_with_contract` retry-on-truncation helper (`llm/schemas.py`) — the same pattern already used by extraction/entity-resolution/entity-revision, not a new mechanism. The title prompt bakes in explicit rules: `yyyy-mm-dd`-prefixed, descriptive enough to identify the source from a search result, under 60 characters, not a full sentence, not generic. The abstract targets roughly 300 characters — dense enough for retrieval, not a summary.

`prepare_capture` gains a required `client: ModelClient` parameter, matching `prepare_enrichment`'s existing signature shape; `wakil ingest transcript/text/article` now resolve a model client the same way `wakil enrich` already does, failing with the same "needs a model provider" message (adapted wording) when none is configured.

Two things stay deliberately unchanged to keep this addition narrow:

- **The raw file's path/slug stays fully deterministic.** The filename-derived (or article-scraped) title is kept as a separate `slug_source` value used only by `_build_raw_file`'s slug computation; `proposal.title` is overwritten with the model's title only after the content-hash duplicate check passes, and only feeds the frontmatter `title:` key (and the DB `Source.title` column) — never the file path. This preserves capture's idempotent-by-content-hash dedup and avoids wasting a model call on a source that turns out to be a duplicate (the model call happens after the dedup check, not before).
- **`source.yaml`'s `abstract` field is added to the base `fields` block**, not scoped to a single origin — it applies uniformly across transcript/text/article/manual capture the same way `title` already does.

The abstract is persisted to `Source.metadata_json` (alongside the existing `context`/`meeting_date` keys) so it survives independent of frontmatter parsing, and a `wakil sources backfill-abstract` command lets already-captured sources (identified by a missing `abstract` key in `metadata_json`) get the same title/abstract call retroactively — reading the raw file's existing text, rewriting only the `title:`/`abstract:` frontmatter keys via `python-frontmatter`'s round-trip (`frontmatter.loads`/`frontmatter.dumps(..., sort_keys=False)`, which preserves existing key order and appends new keys), and updating `Source.title`/`metadata_json`. This is explicitly metadata-only: it never calls `prepare_enrichment`/`apply_enrichment`, never creates memories or relationships, and never touches `Source.status`.

## Consequences

- Capture is no longer model-free; `wakil ingest ...` now requires the same model provider configuration `wakil enrich` already requires (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`+`WAKIL_MODEL`). This is a real behavior change for anyone relying on capture working with no provider configured at all.
- Every capture now costs one extra (small, cheap) model call, on top of enrichment's three. Ingest is no longer instant-and-free the way it was — a deliberate tradeoff for search relevance and durable, content-derived context, per the project thesis's "turn raw inputs into durable, searchable memory."
- Titles are now content-derived and consistently shaped (date-prefixed, under 60 characters), improving scannability in `wakil search`/`wakil query` results and in any listing of `sources/`.
- `wakil sources backfill-abstract` gives existing knowledge bases a path to the same benefit without forcing a re-enrichment (which would re-run extraction/entity-resolution and could propose duplicate notes/memories for sources already linked into the KB).
- **Amended 2026-08-09 (issue #172):** an authored `.md` input's own frontmatter is now stripped before hashing, so the content-hash basis for `.md` inputs is the body rather than the raw file. Sources captured before that change carry the old hash, so `prepare_capture` checks both bases when looking for a duplicate — without which every `.md` a user had already ingested would capture a second time, and `apply_capture`'s overwrite guard would not catch it either (the destination slug now derives from the authored title, not the basename). The same amendment skips this ADR's model call entirely when the input already supplies both a title and an abstract, since there is then nothing for it to contribute.
- `prepare_capture`'s signature changed (added a required `client` parameter) in the same PR cycle as another in-flight change to the same function (adding referenced-paths/digest handling for "prioritize referenced notes in related search") — the two will conflict on merge and need manual reconciliation of the parameter list.

## Implementation

- **[PR #24](https://github.com/ebridges/wakil/pull/24)** — "Generate title/abstract at capture time; add source.yaml abstract field; backfill command."

## Sources

- `src/wakil/app/ingest_service.py` (`prepare_capture`, `_generate_capture_metadata`, `_build_raw_file`, `plan_abstract_backfill`, `apply_abstract_backfill`) and its module docstring
- `src/wakil/llm/schemas.py` (`CaptureMetadata`, `complete_with_contract`)
- `src/wakil/llm/prompts.py` (`build_capture_metadata_prompt`)
- `src/wakil/schema/entities/source.yaml`
- `docs/adr/0008-ingestion-decomposition-reject-multi-agent-mechanism.md` (the prepare/apply, model-call-via-contract conventions this extends)
- `CLAUDE.md`, "Prime Directive" and "Design Biases" (grounded citations, human review, no silent rewriting)
