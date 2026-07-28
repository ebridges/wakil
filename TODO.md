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
- [x] Default-on branch/commit/PR landing (`--local` opts out), tracked per
      **source** rather than per command: `prepare_landing`/`land_ingestion`
      in `git_service.py` reuse (or fetch, or recreate) one branch and one
      PR across a source's whole capture-then-enrich lifecycle, opening the
      PR as a draft after capture and flipping it to ready-for-review with a
      summary comment once enrichment lands, instead of two disconnected
      PRs. Branches now always fork from the resolved default branch
      (`git.resolve_default_branch`), not whatever happens to be checked
      out. Returns to the original branch after landing (or abandoning) a
      change.
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

## MCP interface (docs/adr/0018, docs/adr/0019)

- [x] `wakil mcp serve` (stdio, one workspace per process)
- [x] Read tools: status, search, query, memory_list/show, relationships,
      sources_list/show, git_summary/history, skills_list
- [x] Write tools as prepare/apply pairs: ingest_prepare/apply,
      enrich_prepare/apply, backed by an in-process proposal cache
- [x] `mcp-coordinator` skill for low-friction capture, also served as the
      `wakil://skill/mcp-coordinator` MCP resource
- [ ] Memory lifecycle transition tools (promote/reject/archive) —
      deliberately deferred, not silently bundled into the initial read set
- [ ] Verify the QMD JSON field-name TODO above (Phase 2) also covers the
      `search`/`query` MCP tools, once verified against a real qmd install

## Ingestion/entity refactor (docs/ingestion-refactor-spec.md)

- [x] Phase A: entity schema layer — `schema/entities/*.yaml` (13 types
      transcribed from docs/entity-metadata.md, incl. per-origin source
      sub-schemas), cached loader, `validate_frontmatter()` for new writes
      only (identity/document/hybrid name-title rules, unknown type =
      explicit error, extras tolerated)
- [x] Phase B: Alembic setup (baseline anchor + upgrade-on-open for legacy
      DBs) + `Memory.event_date`,
      `Relationship.subject_note_id`/`object_note_id`
- [x] Phase D-cheap: `wakil schema migrate` — cheap-tier fixes (field
      renames like `end_date`→`end-date`, exact-duplicate drops with
      ambiguity skips, organization/ retyping, title-caser repair, quoted
      `type:` normalization) behind per-type confirm / `--dry-run` /
      `--yes` / `--commit`
- [x] Phase C: enrichment DAG — skills/{transcript,article,text,
      entity-resolve}/SKILL.md (prose judgment only), Pydantic contracts
      (`ExtractionOutput`/`EntityResolution`) injected into the system
      prompt via `model_json_schema()`, validate + one retry + visible
      failure (retired `INGEST_SYSTEM_PROMPT`/`parse_ingest_response`),
      always-invoked entity-resolution call producing `stub_entities`,
      `validate_proposal()` (schema check on new writes, hard stop on
      missing schema, 1:N routing), `Memory.event_date` persisted
- [ ] Phase D-expensive: learning-agenda retyping + reflections move
- Deferred per spec: `wakil entity compile`, memory-vocabulary
  reconciliation, provider-native structured output, runtime plugin loading

## Cross-cutting

- [ ] Read and prioritize `AGENTS.md`, `SCHEMA.md`, `RESOLVER.md` as workspace
      context in queries and note proposals (detection is done)
- [x] Skill resolver: precedence-ordered discovery/override for
      Claude-format `SKILL.md` files (`wakil skills list/which/validate`)
- [x] Alembic migrations once the schema starts evolving (landed with the
      ingestion/entity refactor Phase B)
- [x] Concurrent-ingest safety across git worktrees: `WorkspaceConfig` now
      resolves a `state_root` (via `.git`'s common-dir, shared by a repo's
      main worktree and every `git worktree add`-linked one) distinct from
      `root_path` (this checkout's own directory for file I/O) — a
      `Workspace` DB row, content-hash dedup, and the FTS/note index are
      keyed on `state_root`, so N linked worktrees running `wakil
      ingest`/`wakil enrich` concurrently share one workspace instead of
      each silently getting its own empty one (confirmed empirically: 3-way
      concurrent ingest across worktrees, one shared `Workspace` row, no
      note loss). `index_notes` no longer prunes missing notes when run
      from a linked worktree (a note absent from *this* worktree's checkout
      may just be on another worktree's branch, not actually gone).
      `database.py` now sets `PRAGMA journal_mode=WAL` +
      `PRAGMA busy_timeout=30000` on every connection, so a writer that
      loses the race for SQLite's single write lock waits instead of
      immediately erroring — verified sessions never span slow work (LLM
      calls, network fetches), so that lock is only ever held for a
      handful of INSERT/UPDATE statements, not an ingest's full duration.
- [x] `Source.content_hash` dedup race (flagged above, now closed):
      `uq_sources_workspace_content_hash` (migration 0004, dedupes any
      pre-existing collisions and repoints referencing memories/
      relationships/ingest_runs onto the survivor before adding the
      constraint) plus `apply_capture` catching the resulting
      `IntegrityError` — cleans up the raw file it already wrote, reports
      "already ingested" the same way an early duplicate-of hit is
      reported, and the CLI returns to the original branch on that failure
      (`_run_ingest`'s `prepare_landing`/`apply_capture` calls split into
      separate try blocks so `abandon_landing` always has a defined
      `landing` to fall back to). Verified empirically: concurrent
      identical-content ingest across two worktrees now leaves exactly one
      `Source` row and both worktrees clean on their own branches.
- [ ] Docs: QMD integration, memory lifecycle, git workflow
- [ ] Wikilink/tag extraction during indexing (feeds relationship discovery)
- [ ] FUTURE: full agentic eval harness. The current live-model skill evals
      (`tests/evals/runner.py`, `-m eval`) are single-shot: skill prose +
      query go to a model, the model *describes* what it would do, and a
      second model call grades that description against a rubric — no real
      tool use, no actual file mutation. A more faithful harness would run a
      real agent with real tool access against a scratch workspace and grade
      the resulting diff. That needs sandboxing, a tool-call loop, and
      diff-based grading — materially more infrastructure than was justified
      for the initial 12-skill suite, so it's deliberately deferred.

## Deliberately deferred (see PROMPT.md non-goals)

- Neo4J, background daemon, web UI, multi-user auth, audio transcription
