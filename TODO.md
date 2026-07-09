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

- [x] Source records for ingests (`wakil ingest`)
- [x] Text/transcript ingest (including .srt subtitle stripping)
- [x] Article URL ingest (httpx + readability-lxml)
- [x] Candidate memory extraction
- [x] Candidate relationship extraction
- [x] Related note search during ingest
- [x] Markdown note proposal with preview and confirmation
- [ ] Whisper zip archive ingest (transcript JSON)
- [ ] Twitter/X URL ingest (metadata-only)
- [ ] Pasted-text ingest from stdin
- [ ] Consult SCHEMA.md/RESOLVER.md content when proposing note paths
      (currently: model suggests, wakil validates + falls back to drafts/)

## Phase 4: Git-Native Changes

- [x] Ingest branch creation (`wakil/ingest/<date>-<slug>`)
- [x] Commit convention helpers (`wakil ingest:`, `wakil note:`, ...)
- [x] Dirty-tree checks before branching
- [x] Optional gh-based PR creation (`--pr`)
- [x] `wakil git summary` and `wakil git history <path>`
- [ ] Return to the original branch after `--branch` ingest (currently stays
      on the wakil branch, matching manual git workflows)
- [ ] `wakil note:`/`wakil link:` commit flows once note editing lands

## Phase 5: Memory Lifecycle

- [x] `wakil memory list/show/promote/reject/archive`
- [x] Fading in retrieval ranking (state ordering, working memories fade
      after 30 days, archived downranked, rejected excluded)
- [x] Memory citations in query answers (memory:<id> refs, last_seen_at
      bumped when used)
- [ ] Use last_seen_at/reference counts in ranking (currently recorded but
      only created_at feeds fading)
- [ ] `wakil memory add` for manually authored memories
- [ ] Vector/embedding similarity for memories (optional, per PROMPT)

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
