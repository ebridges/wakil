---
title: Troubleshooting
status: living
audience: wakil design
---

# Troubleshooting

A dated, append-only log of real bugs and gotchas encountered while building
`wakil` — not a general FAQ. An entry only belongs here if it (a) cost real
debugging time, (b) wasn't obvious from reading the code, and (c) would
plausibly recur or mislead someone again. Most work sessions should add
nothing; see the `development-docs` skill (`.claude/skills/development-docs/SKILL.md`) for
the judgment process that maintains this file. Every entry cites a concrete
source (commit SHA, PR #, or session detail) for traceability.

### Unquoted `[[wikilink]]` in YAML frontmatter parses as a nested list
**Date:** 2026-07-18 · **Source:** session 0062da63-7923-40bd-b839-86cf8fb1d21f

**Symptom:** An entity-revision smoke test kept failing with `validate_proposal` rejecting a `ref` field (e.g. `company`) as "expected a reference string, got list," even after the model's proposal was fixed — looking like a wakil merge/validation bug.

**Root cause:** The test fixture's frontmatter had been hand-written with an unquoted wikilink, `company: [[companies/acme.md]]`. In YAML, unquoted `[[...]]` is flow-sequence syntax, not a wikilink string, so it parses as a nested list (`[['companies/acme.md']]`) before any wakil code sees it. Obsidian's `[[wikilink]]` convention only has meaning in Markdown body text — in raw YAML frontmatter it must be quoted (`company: "[[companies/acme.md]]"`), which is how real model-generated notes already format it.

**Fix:** Quote wikilink-valued frontmatter fields as strings when hand-authoring fixtures or notes. If you see a `ref`-field validation error blaming a list value, check the source frontmatter for an unquoted `[[...]]` before suspecting the ingest/merge code.

### FTS5 snippet() highlighting the wrong column
**Date:** 2026-07-17 · **Source:** `src/wakil/storage/fts.py` (commit 20b75a6)

Search snippets in `search_notes`, `search_memories`, and `search_sources` could highlight the wrong part of a result. The `snippet()` calls hardcoded the column-index argument to `0`, which SQLite FTS5 takes literally as "always highlight the first column" — `title` in `notes_fts` (`["title", "path", "frontmatter_json"]`) and `sources_fts` (`["title", "origin", "author"]`) — rather than the column that actually matched the query. Since `0` is a valid column index, this ran without error and only surfaced as subtly wrong/misleading snippets. Fix: pass `-1`, FTS5's documented sentinel for "use the best-matching column," in all three `snippet()` calls.

### Deleted/recreated remote leaves local README unrecoverable via fetch/pull
**Date:** 2026-07-09 · **Source:** session 23a47c01-9864-400e-b831-5c3e3fd6994d

Symptom: after deleting and recreating the GitHub remote and re-running `git fetch`/`git pull`, the local `README.md` was gone, replaced by whatever the recreated remote's initial commit contained (in this case just `.gitignore`). Root cause: the recreated repo's initial commit (`2e10ed7`) has no shared history with the original local commit (`469011b`), so pulling force-overwrote the working tree with the new remote's tree instead of merging. Fix: the original commit was still sitting in the local object database as a dangling commit (not yet garbage-collected), so it could be recovered with `git show 469011b:README.md > README.md` and then pushed back (force-push, since histories diverged). This only works if the dangling commit hasn't been pruned yet — recover promptly, and avoid deleting/recreating remotes as a way to "reset" a repo.

### Rich console markup mangles wikilink warnings
**Date:** 2026-07-17 · **Source:** `src/wakil/ui/console.py` (warning print sites); session `93aecd7b-3db1-4708-aaf6-81eeb2653a37`

Warning messages containing wikilinks (e.g. `[[people/eleni-karahalios|Eleni Karahalios]] -> [[people/eleni-karahalios.md|Eleni Karahalios]]`) rendered as mangled `[] -> []` output instead of the real link text. Root cause: these warnings were interpolated into f-strings passed straight to `console.print(...)`, and Rich's console treats any bare `[...]` in printed text as a style tag, not literal content — so the wikilink brackets were parsed and swallowed as markup rather than displayed. This was only caught by live/manual verification, not by unit tests, because existing tests asserted on `proposal.warnings` list contents but never exercised the actual `console.print` render path. Fix: escape dynamic content with `rich.markup.escape()` before interpolating it into any markup-enabled `console.print()` call (`from rich.markup import escape; console.print(f"[yellow]warning:[/yellow] {escape(warning)}")`), and add a regression test that renders through `console.print` and inspects captured stdout. The same risk applies to any other dynamic, bracket-containing text (titles, paths, model output) printed elsewhere in `console.py` — each call site needs the same escaping unless it's a fixed literal.

### Entity-link reconciliation false-corrected links over `.md` suffix convention
**Date:** 2026-07-17 · **Source:** `src/wakil/app/ingest_service.py` (`_normalize_link_path`, `_deslug`); session `93aecd7b-3db1-4708-aaf6-81eeb2653a37`

Symptom: a live `enrich` run logged reconciliation "corrections" such as `[[people/eleni-karahalios|Eleni Karahalios]] -> [[people/eleni-karahalios.md|Eleni Karahalios]]` even though the original link already pointed at the right note. Root cause: reconciliation compared the extraction-written link path directly against entity-resolution's `target_note_path` (always `.md`-suffixed, matching `Note.path`) and rewrote any link whose extension didn't match — but this KB genuinely mixes both the `[[people/x]]` and `[[sources/y.md]]` conventions, so a trailing-`.md` mismatch alone doesn't mean the link is wrong. Fix: added `_normalize_link_path()` to strip a trailing `.md` before comparing paths, so a link is only rewritten when it targets a genuinely different entity, not just a different extension style. Watch for this same pitfall anywhere else in the codebase that compares a stored `target_note_path`/`Note.path` against a raw wikilink path without normalizing the `.md` suffix first.

### Ingested source frontmatter fields don't match the vault's schema

**Date:** 2026-07-20 · **Source:** `src/wakil/app/ingest_service.py` (`_build_raw_file`, ~line 1301)

Non-transcript raw captures written by `_build_raw_file` come out with frontmatter fields (`type: source`, `source_type:`, `origin:`, `title:`, `retrieved:`) that are hardcoded in the function rather than derived from the target vault's actual `source` schema. In at least one real vault, the schema uses `captured:` for the same concept, not `retrieved:`, so every non-transcript ingest silently writes non-conformant frontmatter into `sources/`. This isn't visible from reading `ingest_service.py` alone — it only surfaces by diffing the hardcoded keys against the vault's own `schema.md`. Fix by sourcing these field names from the vault's schema/skill definitions (as the transcript branch and `_KNOWN_FIELD_VALUES` mapping already do) instead of hardcoding them in `_build_raw_file`.

### Workspace guide files (SCHEMA.md, RESOLVER.md) are silently truncated at 4,000 chars, with no warning

**Date:** 2026-07-20 · **Source:** `src/wakil/app/ingest_service.py:70` (`GUIDE_MAX_CHARS = 4_000`), `src/wakil/app/ingest_service.py:1221-1231` (`load_workspace_guides`)

**Mechanism:** `load_workspace_guides()` reads each of `SCHEMA.md` and `RESOLVER.md` (if present under the workspace root) and slices it with `[:GUIDE_MAX_CHARS]`, where `GUIDE_MAX_CHARS = 4_000`. This is a blind character-count cutoff: anything past char 4,000 is dropped before the guide content ever reaches the model, with no truncation warning, log message, or CLI-visible indication that it happened. There is no test coverage for this path (`tests/fixtures/kb/SCHEMA.md` is only 96 chars, well under the cutoff, so the truncation branch is never exercised).

**Implication:** If a workspace's `SCHEMA.md` or `RESOLVER.md` grows past 4,000 characters, any frontmatter fields or routing rules declared after that point are silently dropped from what the model sees during ingest — with nothing in the output to indicate the guide was cut off. This is easy to miss because the file looks complete when opened in an editor.

**Fix / workaround:** If ingest behavior seems to ignore rules or fields defined later in `SCHEMA.md`/`RESOLVER.md`, check the file's character count against the 4,000-char `GUIDE_MAX_CHARS` limit first. Until a truncation warning is added, keep guide files under ~4,000 chars (front-load the most important rules), or raise `GUIDE_MAX_CHARS` in `ingest_service.py`.

### Entity resolution misses names that only appear in the filename, not the transcript body

**Source:** `src/wakil/app/ingest_service.py:474` (`_candidate_entity_notes`), `:458` (`_title_terms`); introduced together with the entity-name-match feature in commit `ff3c24f` ("find existing entity pages by name, not just by relevance"); session transcript `~/.claude/projects/-Users-ebridges-Projects-wakil/93aecd7b-3db1-4708-aaf6-81eeb2653a37.jsonl`

Symptom: ingesting a whisper transcript proposed a duplicate entity note for a company/person that already had a page in the workspace. Root cause: `_candidate_entity_notes()` builds its proper-noun candidates via `_PROPER_NOUN_RE`, which only scans the transcript/context text and requires capitalized words — it never looks at the capture's filename/title. When the entity name is never spoken in the dialogue and appears only in the filename, the existing entity page is never offered as a candidate, so entity resolution proposes a new stub instead of reusing the existing note. Fix: `_title_terms()` lowercases and tokenizes the humanized filename/title (stripping a small stopword list of generic words like "sync"/"offer"/"recap") and the caller passes the result into `_candidate_entity_notes(..., extra_terms=_title_terms(title))`, which merges those terms into the candidate set, bypassing the capitalized-proper-noun requirement for text sources where casing can't be trusted as a name signal.

Note: this fix already landed in the codebase (`git log -S "_title_terms"` shows it introduced whole in commit `ff3c24f`); the surviving value of this entry is documenting *why* filename-derived candidates are needed at all, for anyone touching `_candidate_entity_notes`/`_title_terms` later.

### `_rewind_to_legacy` test helper's table-rebuild silently killed autoincrement (id=NULL)

**Status:** fixed only on the open `worktree-git-integration` branch (PR #15, commit `a14af48`), not yet on `main`.

**Source:** `tests/unit/test_migrations.py` (`_rewind_to_legacy`)

The `_rewind_to_legacy` test helper simulates a pre-Alembic `wakil.db` by rebuilding `memories`/`relationships`/`sources` with `CREATE TABLE ... AS SELECT` (needed because SQLite can't `DROP COLUMN` on FK-referenced columns). That technique never preserves `id`'s `PRIMARY KEY`/autoincrement status, so it silently degraded `id` into a plain column disconnected from SQLite's rowid/autoincrement — every row inserted into a rebuilt table afterward got `id=NULL`, while SQLAlchemy masked this by reporting the unrelated internal rowid as if it were `id`.

It went unnoticed until the new `test_migration_dedupes_existing_content_hash_collisions` test (added for the content-hash dedup race fix) needed real, distinct ids after a rewind. The real pytest run first failed with `sqlalchemy.exc.IntegrityError: UNIQUE constraint failed: sources.workspace_id, sources.content_hash` on the `CREATE UNIQUE INDEX` step of migration 0004 — misleading, since the test's two `Source` rows were meant to collide on `content_hash` but not on `id`. A follow-up standalone debug script hit an unrelated `sqlite3.OperationalError: duplicate column name: event_date`, which was a self-inflicted mistake in the debug script (it upgraded a database without first calling `_rewind_to_legacy`), not a symptom of this bug. A corrected debug script reproduced the same `UNIQUE constraint failed` error, which is what led to inspecting `_rewind_to_legacy` and finding that every id involved was `NULL`.

Fixed at the root by explicitly declaring `id INTEGER PRIMARY KEY` in the rebuilt table and populating it via a separate `INSERT ... SELECT` rather than relying on `CREATE TABLE ... AS SELECT` to carry primary-key-ness over.

Since this fix lives only on an unmerged branch, treat it as still-open until PR #15 lands on `main` — if `_rewind_to_legacy` is copied or ported before then, carry the fix with it.

### Git worktrees fix the ingest lock race, but wakil's workspace identity isn't worktree-aware

**Context:** During work on PR #15, testing showed that two simultaneous `wakil ingest` calls against one shared checkout reliably fail: both call `prepare_landing()` → `git switch -c <branch>`, and one process loses the race for `.git/index.lock` every time (every trial in that test). The user then asked, proactively and hypothetically: "what if each ingest is done on a separate worktree — would that allow them to be operated on in parallel?" That question prompted the investigation below; it was not a reported bug.

**Finding:** Separate git worktrees do solve the lock race — each linked worktree has its own index file, so `git switch`/`git add`/`git commit` in worktree-a and worktree-b don't contend for the same `.git/index.lock`. Across roughly 10 trials of concurrent ingest using two real `git worktree`-created checkouts, no lock collision occurred.

But that alone doesn't make worktrees safe to use, because wakil's own database isn't worktree-aware. `_ensure_workspace()` in `src/wakil/app/workspace_service.py` (main, lines 96–110) looks up the `Workspace` row with an exact string match on `config.root_path`:

```python
workspace = session.scalar(
    select(Workspace).where(Workspace.root_path == str(config.root_path))
)
```

Since each linked worktree is a different directory, the first touch from a second worktree doesn't match the existing row and silently creates a new, disconnected `Workspace` — no error, just fragmented state. This was confirmed by direct inspection of the shared SQLite file, which showed two separate `Workspace` rows for what was meant to be one repository (one keyed to the main checkout's path, one to the worktree's path). The same `root_path`-keyed pattern recurs anywhere state is scoped per workspace (change tracking, content-hash dedup, the FTS/QMD index), so a naive worktree workflow trades a loud, safe failure (lock contention, retry-able) for a silent, worse one (siloed sources/memories/PR-tracking state per worktree, no error raised).

**Fix (implemented on the `worktree-git-integration` branch / PR #15, not yet merged to `main` as of 2026-07-20):** `WorkspaceConfig` now separates `root_path` (this checkout, used for file I/O) from `state_root` (where `.wakil/` lives and what a `Workspace` row is keyed on), resolved via `resolve_state_root()` in `src/wakil/config/settings.py`. For a linked worktree, `resolve_state_root()` uses `worktree_anchors()` to find the git common-dir and returns its parent (the main worktree's root); otherwise it returns `root_path` unchanged, so non-worktree usage is unaffected. All lookup sites now key on `state_root` instead of `root_path`. The branch also adds SQLite `WAL` mode plus a 30s `busy_timeout` so a writer that still loses a lock race waits instead of failing immediately, and stops `index_notes` from deleting notes just because they're absent from the current worktree's checkout.

**Why this is worth recording:** the failure mode is silent (no exception, no error path) and specific to a workflow (parallel ingest via worktrees) that this project's own agent-API plans (multiple callers hitting the same workspace concurrently) make plausible again later — worth knowing before anyone reaches for "just use worktrees" as the fix for the lock race.

### Duplicate Source rows under concurrent ingest (content-hash dedup race) — resolved

**Source:** `worktree-git-integration` branch (PR #15, still open/draft as of 2026-07-20 — not yet merged to `main`), fix commit `a14af48` ("fix(ingest): close the content-hash dedup check-then-insert race")

Under real concurrent `wakil ingest` calls, two processes ingesting identical content could both create their own `Source` row instead of one being flagged as a duplicate. Root cause: `prepare_capture`'s dedup check reads for an existing `content_hash` and `apply_capture` inserts afterward — a check-then-act (TOCTOU) race, since `content_hash` had no unique constraint at the DB level. Reproduced live (two worktrees, identical content, both got "Captured source #N" instead of the second reporting "Already ingested").

Fixed in `a14af48`:
- Migration `0004_source_content_hash_unique.py` adds `uq_sources_workspace_content_hash`, a unique index on `Source(workspace_id, content_hash)`. It first dedupes any pre-existing collisions — repointing `memories`/`relationships`/`ingest_runs` rows that reference a duplicate onto the earliest (lowest id) survivor — before adding the index, so it's safe to run against a database that already hit the race.
- `apply_capture` (`src/wakil/app/ingest_service.py`) wraps the `session.flush()` insert in a `try`/`except IntegrityError`: on the constraint violation it rolls back, deletes the raw file it had already written (avoiding an orphaned file with no `Source` row), looks up the winning `Source.id`, and raises the same "Source already ingested" error a normal duplicate-of hit would produce.

`TODO.md` on that branch marks this item `[x]` closed as of `a14af48`. This bug predated the git-worktree/state_root work and was previously masked because one of two racing `wakil ingest` processes almost always lost the `.git/index.lock` race first and exited before reaching the dedup check — concurrent ingest across worktrees is what made the race reachable in practice.

Note: as of 2026-07-20, PR #15 (which contains this fix along with the broader default-on git-landing work) has not been merged into `main`.

---

### `EOF while parsing a string` from a Pydantic validation error may mean the model response was truncated, not malformed

**Date:** 2026-07-20 · **Source:** `fix/entity-revision-truncation-errors` branch — `ModelTruncatedError` in `src/wakil/llm/client.py`, retry handling in `complete_with_contract` (`src/wakil/llm/schemas.py`)

`wakil enrich` failed with `ModelContractError: EntityRevisionOutput: model output failed validation twice: 1 validation error for EntityRevisionOutput\n  Invalid JSON: EOF while parsing a string ...`. This reads like the model produced garbage JSON, but the actual cause was `_run_entity_updates` (`src/wakil/app/ingest_service.py`) batching every `action=update` entity — 7 of them, several with long histories — into one call that asks the model to re-synthesize each note's full compiled-truth body, which exceeded the fixed `max_tokens=8192` and cut the response off mid-string. Neither `AnthropicClient` nor `OpenAICompatibleClient` (`src/wakil/llm/client.py`) checked `stop_reason`/`finish_reason` for this case, so a truncated response was indistinguishable from a genuinely malformed one until someone counted characters against the token budget.

Fixed by raising a distinct `ModelTruncatedError` when the provider reports `stop_reason == "max_tokens"` / `finish_reason == "length"`, and having `complete_with_contract`'s retry (`src/wakil/llm/schemas.py`) double the token budget and resend the same prompt on truncation, instead of treating it like invalid JSON (appending the error and resending unchanged, which just truncates again at the same length). If a future model-contract call raises this same low-level symptom, check whether the call batches many items into one response before assuming the prompt or schema is wrong.

---

### "doesn't match the expected H1 / 'Timeline / Log' shape" warning on real, otherwise-normal entity notes

**Date:** 2026-07-20 · **Source:** `fix/entity-revision-truncation-errors` branch — `_TIMELINE_HEADING_RE` in `src/wakil/app/ingest_service.py`

`wakil enrich` warned that `people/edward-bridges.md` didn't match the expected shape and left it unchanged. `_merge_entity_note`'s shape check required the exact heading `## Timeline / Log` (per `SCHEMA.md`), but the file just has `## Timeline`. A repo-wide check found this isn't a one-off: 36 entity notes across `people/` and `companies/` use the bare heading, predating when `/ Log` became the documented convention — every one of them would silently fail to update on every future `enrich` run, with only a generic shape-mismatch warning to go on.

Fixed by loosening `_TIMELINE_HEADING_RE` to accept `## Timeline` with an optional ` / Log` suffix, rather than editing the 36 KB notes to match — per this project's "never silently rewrite user knowledge" rule, a code-level tolerance fix is preferable to a bulk content migration nobody asked for. If a new heading-shape warning appears again, check for this kind of convention drift across the KB (`grep -rlE '^## Timeline\s*$'`) before assuming it's a one-off malformed note.

---

### "updated: required field is missing" after an otherwise-successful entity-note merge

**Date:** 2026-07-20 · **Source:** `fix/entity-revision-truncation-errors` branch — `_merge_entity_note` in `src/wakil/app/ingest_service.py`

After fixing the heading-shape warning above, re-running `wakil enrich` against `people/edward-bridges.md` got further but then failed proposal validation: `updated: required field is missing`, and nothing was written. `person`/`company`/`concept`/`project` all declare `updated` as a required frontmatter field, but `_merge_entity_note` only bumped it when the key was already present (`if "updated" in metadata: metadata["updated"] = today`) — it never added the key. `edward-bridges.md`'s frontmatter has `created` but no `updated` at all, so the merge left the required field permanently missing, no matter how many times you re-ran enrich. A repo-wide check found 48 more notes in the same state (11 people, 35 companies, 2 concepts).

Fixed by stamping `metadata["updated"] = today` unconditionally on every merge — the function only runs when `has_update=True`, so "we just updated this note" is never a special case. If a future proposal-validation error names a required field that a merge *should* be setting, check whether the merge logic conditions on the field already existing rather than always writing it.

