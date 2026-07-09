# TODO

Tracked future work, ordered roughly by the build phases in `PROMPT.md`.

## Phase 2: Search and Query

- [ ] QMD search wrapper (`integrations/qmd.py`) and `wakil search`
- [ ] SQLite FTS5 over memories and sources
- [ ] Minimal model client abstraction (Anthropic + OpenAI-compatible)
- [ ] `wakil query` using QMD results + memory search, with citations
- [ ] Follow-up query suggestions

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
