# TODO

Tracked future work, ordered roughly by the build phases in `PROMPT.md`.

## Phase 2: Search and Query

- [x] QMD search wrapper (`integrations/qmd.py`) and `wakil search`
- [x] SQLite FTS5 over notes, memories, and sources
- [x] Minimal model client abstraction (Anthropic + OpenAI-compatible)
- [x] `wakil query` using QMD results + memory search, with citations
- [x] Follow-up query suggestions (prompted as part of the answer)
- [ ] Verify QMD JSON field names against a real qmd install (parser is
      defensive but untested against the actual binary)
- [ ] Memory-aware ranking in query context selection (state/importance
      weighting comes with Phase 5)

## Phase 3: Ingest

- [ ] Source records for ingests (`wakil ingest`)
- [ ] Text/transcript ingest
- [ ] Article URL ingest (httpx + readability-lxml)
- [ ] Candidate memory extraction
- [ ] Candidate relationship extraction
- [ ] Related note search during ingest
- [ ] Markdown note proposal with diff preview

## Phase 4: Git-Native Changes

- [ ] Ingest branch creation (`wakil/ingest/<date>-<slug>`)
- [ ] Commit convention helpers (`wakil ingest:`, `wakil note:`, ...)
- [ ] Dirty-tree checks before writes
- [ ] Optional gh-based PR creation
- [ ] `wakil git summary`

## Phase 5: Memory Lifecycle

- [ ] `wakil memory list/promote/reject/archive`
- [ ] Fading in retrieval ranking (state, importance, freshness, recency)
- [ ] Memory citations in query answers

## Phase 6: Dream

- [ ] `wakil dream --recent` report-only synthesis
- [ ] Topic-based dream, `--write-report` mode

## Cross-cutting

- [ ] Read and prioritize `AGENTS.md`, `SCHEMA.md`, `RESOLVER.md` as workspace
      context in queries and note proposals (detection is done)
- [ ] Skills loader for Claude-format `SKILL.md` files
- [ ] Alembic migrations once the schema starts evolving (create_all is enough for now)
- [ ] Docs: QMD integration, memory lifecycle, git workflow
- [ ] Wikilink/tag extraction during indexing (feeds relationship discovery)

## Deliberately deferred (see PROMPT.md non-goals)

- Neo4J, background daemon, web UI, multi-user auth, audio transcription
