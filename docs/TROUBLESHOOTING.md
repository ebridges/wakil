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

### Commit-message emoji was a "manual commits only" presentation layer, so automatic commits silently lacked it
**Date:** 2026-07-23 · **Source:** `src/wakil/app/git_service.py`, `src/wakil/skills/kb-commit/SKILL.md`

**Symptom:** Commits made by `wakil ingest --commit`/`--branch`/`--pr` (on by default per ADR 0003) showed up in `git log` with no emoji (e.g. `wakil source: add mosaic offer sync ian gutwinski`), while the `kb-commit` skill's documented examples all showed an emoji-prefixed subject (`📥 wakil source: ...`) — read as a bug from the user's side.

**Root cause:** `commit_message()` deliberately returned the bare `wakil <kind>: <description>` string; `kb-commit/SKILL.md` described the emoji as a "presentation layer this skill adds on top of `commit_message()`'s output," applied only when a commit is hand-constructed by that skill, specifically to keep the function's own output unaffected. In practice all wakil-generated commits land in the same `git log`, so this was an inconsistency a user would see immediately, not an internal implementation seam — and no code anywhere actually parsed the bare format (confirmed by a full-codebase search before fixing), so the split had no real justification.

**Fix:** Moved the emoji into `commit_message()` itself (`COMMIT_EMOJI` dict, keyed by kind) so every wakil-generated commit — automatic CLI flag or `kb-commit`'s manual commit — carries the same prefix. When a "presentation layer added on top of a shared helper" is actually visible to the end user (not just internal/programmatic consumers), prefer folding it into the helper rather than splitting it across two call paths that both write to the same user-visible log.

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

### `frontmatter.dumps()` alphabetizes keys unless `sort_keys=False` is passed explicitly
**Date:** 2026-07-21 · **Source:** `src/wakil/app/ingest_service.py` (`apply_abstract_backfill`)

**Symptom:** A metadata-only rewrite (`frontmatter.loads(raw)` → mutate a couple of keys → `frontmatter.dumps(post)`) was meant to change only the touched keys, but the round trip silently reordered every other frontmatter field alphabetically, producing a large, misleading diff for what should have been a two-line change.

**Root cause:** `python-frontmatter`'s `dumps()` forwards `**kwargs` to its YAML handler, which defaults to `yaml.dump`'s own default (`sort_keys=True`) when no override is given — there's no hint of this in `frontmatter`'s own API surface, only in the underlying PyYAML behavior. This is presumably why every other frontmatter-writing call site in this codebase (`_build_stub_entities`, `_stub_content`, `schema_migrate_service`) bypasses `frontmatter.dumps()` entirely and calls `yaml.safe_dump(metadata, sort_keys=False, ...)` directly.

**Fix:** Pass `sort_keys=False` explicitly to `frontmatter.dumps(post, sort_keys=False)` for any future round-trip edit that needs to preserve existing key order (new keys still append at the end, in insertion order) — verified interactively before relying on it in `apply_abstract_backfill`.

### Workspace guide file (RESOLVER.md) is silently truncated at 4,000 chars, with no warning

**Date:** 2026-07-20 · **Updated:** 2026-07-21 (`docs/adr/0011-retire-schema-md-dependency.md` removed `SCHEMA.md` from `load_workspace_guides` entirely — this note now applies to `RESOLVER.md` only) · **Source:** `src/wakil/app/ingest_service.py:70` (`GUIDE_MAX_CHARS = 4_000`), `load_workspace_guides`

**Mechanism:** `load_workspace_guides()` reads `RESOLVER.md` (if present under the workspace root) and slices it with `[:GUIDE_MAX_CHARS]`, where `GUIDE_MAX_CHARS = 4_000`. This is a blind character-count cutoff: anything past char 4,000 is dropped before the guide content ever reaches the model, with no truncation warning, log message, or CLI-visible indication that it happened. There is no test coverage for this path.

**Implication:** If a workspace's `RESOLVER.md` grows past 4,000 characters, any routing rules declared after that point are silently dropped from what the model sees during ingest — with nothing in the output to indicate the guide was cut off. This is easy to miss because the file looks complete when opened in an editor.

**Fix / workaround:** If ingest behavior seems to ignore rules defined later in `RESOLVER.md`, check the file's character count against the 4,000-char `GUIDE_MAX_CHARS` limit first. Until a truncation warning is added, keep the guide file under ~4,000 chars (front-load the most important rules), or raise `GUIDE_MAX_CHARS` in `ingest_service.py`.

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

---

### `enrich` replaced an entire "## Compiled Truth" section with nothing (the clobbering bug, in wakil's own merge code)

**Date:** 2026-07-20 · **Source:** `fix/entity-revision-truncation-errors` branch — `_merge_entity_note` in `src/wakil/app/ingest_service.py`

After the two fixes above got `wakil enrich` writing again, the diff preview for `companies/mosaic-private-markets.md` showed its entire `## Compiled Truth` section — a long, carefully synthesized paragraph-and-bullet block — replaced by nothing, so the file went straight from the H1 to `## Timeline / Log`. Cause: the model returned `has_update=True` (a new Timeline entry was warranted) with an empty/absent `compiled_truth` (nothing about the State needed re-synthesis) — a legitimate combination — but `_merge_entity_note` read "no compiled_truth" as "the top section is now empty" rather than "no change to the top section," and wrote exactly that. This is the precise "clobbering bug" `note-revision/SKILL.md` warns about (full-file-style regeneration silently dropping prior content), except living in wakil's own merge code rather than in model behavior — a diff-preview review would have caught it (per that skill's "diff your draft, stop if it's shorter" step), but it's better to not produce the bad diff in the first place.

Fixed by falling back to the existing top section (stripping its own trailing `---` divider) whenever `compiled_truth` is empty, instead of dropping it. If a future entity-update diff shows content vanishing rather than being added to, check whether the responsible field was actually empty in the model's response before assuming the prompt is at fault.

---

### CI-only test failures after a command starts requiring a model provider (a local API key masks the gap)

**Date:** 2026-07-21 · **Source:** PR #24, `feat/capture-time-title-abstract` — `test_qmd_cli.py`

`docs/adr/0010` made `wakil ingest` require a resolved `ModelClient` (capture now generates title/abstract). Two pre-existing tests in `tests/integration/test_qmd_cli.py` invoke `wakil ingest transcript` without mocking `wakil.llm.client.resolve_client`. They passed in the PR author's own environment — and were self-reported as "pytest clean" — because a real `ANTHROPIC_API_KEY` happened to be set there, so `resolve_client()` silently succeeded against the live API. CI has no such key, so both failed there with "Ingest needs a model provider," exit code 1.

If a command starts requiring a model provider for the first time, grep the whole test suite for CLI invocations of that command (`runner.invoke(app, [..., "ingest"/"enrich", ...])`) — not just the test file for the change being made — since any of them can be silently relying on a real key rather than a mock. Don't trust a "pytest clean" self-report (agent or human) that wasn't run with API-key env vars explicitly unset; `env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY uv run pytest -q` is the way to actually reproduce CI's clean-environment behavior locally.

### A `git commit` after manual conflict-resolution edits can silently omit the last fix (passes locally, fails in CI)

**Date:** 2026-07-22 · **Source:** PR #24, `feat/capture-time-title-abstract` — merge-conflict resolution with `main`

After resolving a merge conflict by hand and fixing two resulting test failures with further edits, `git status` showed `MM tests/unit/test_ingest_service.py` (staged *and* unstaged changes to the same file) immediately before running `git commit -m "..."` — a signal that got missed. Plain `git commit` (no `-a`, no prior `git add`) only commits the staged half; the unstaged edits — the actual fix for both failures — never made it into the commit. `pytest` run locally right after still passed, because it reads the working tree on disk, not what's actually in the commit, so the gap was invisible until CI (which checks out the real commit) failed with the exact same two pre-fix errors.

After any manual multi-file edit immediately followed by `git commit`, check `git status` for a lingering `M` in the *unstaged* column on a file just edited — `MM` (or a bare unstaged `M` right after an edit) means `git add` didn't pick up the latest change. `git commit -a` (for tracked files) or an explicit `git add <file>` right before `git commit` removes the ambiguity. More generally: a local "tests pass" result is a claim about the working tree, not about the commit that was just made — if something depends on the commit specifically (CI, a fresh clone, a teammate's pull), diff `HEAD` against the working tree (`git diff HEAD -- <file>`) before trusting that the commit is complete.

### Resolving a PR's merge conflict in the wrong worktree (similarly-named branch, no real connection to the PR)

**Date:** 2026-07-22 · **Source:** PR #25 (`fix/eval-suite-flakiness-and-skill-gaps`) merge-conflict resolution, during a multi-PR merge session with several parallel worktrees

While merging six PRs in sequence, PR #25's conflict-resolution work was done inside `.claude/worktrees/wf_7ab073ba-4f1-2` — which was actually checked out to `refactor/candidate-notes-stopword-filtering`, a *different*, already-merged-and-closed PR's branch (PR #21), reused from earlier in the session. The mistake wasn't caught until after committing and pushing, because the resolution itself was plausible-looking (real conflict markers, real code, tests passed) — only `git log --oneline` afterward revealed the branch tip didn't match. The push landed on a dead branch with no attached open PR, so it was inert (no real harm), but the actual conflict on PR #25's real branch (`fix/eval-suite-flakiness-and-skill-gaps`, which had no dedicated worktree — its commits were made directly in the main checkout) still needed to be redone from scratch in the right place.

Root cause: `git worktree list`'s auto-generated directory names (`wf_<hash>-N`) don't encode which PR/branch they hold in an obviously-checkable way at a glance, and not every branch in a multi-branch session has a 1:1 worktree — some get created directly in the main checkout instead. Before resolving a specific PR's conflict in a given worktree (or the main checkout), confirm the location's actual branch matches that PR's `headRefName` — `git branch --show-current` in the target directory, cross-checked against `gh pr view <n> --json headRefName` — rather than assuming by directory-naming convention or "the one I used last for something related."

### A Pydantic field literally named `register` silently shadows `ABCMeta.register`

**Date:** 2026-07-23 · **Source:** PR (phase 2 of `docs/adr/0014-memory-register-field-and-query-exclusion.md`), adding `CandidateMemoryModel.register`

Adding `register: Literal["formal", "casual"] | None = Field(...)` to a Pydantic `BaseModel` subclass produced `UserWarning: Field name "register" in "CandidateMemoryModel" shadows an attribute in parent "BaseModel"` on every import, plus a broken `INSERT` once the same name was mirrored onto the SQLAlchemy `Memory` model mid-refactor. Cause: Pydantic's `ModelMetaclass` inherits from `abc.ABCMeta`, which defines a `register` classmethod (for registering virtual subclasses) — a field named `register` shadows it at the class level. Instances still work correctly (`model.register` reads the field, not the classmethod), so this is easy to miss in a quick manual check; only the warning (or a linter treating warnings as errors) surfaces it.

If a Pydantic model needs a field for a "register"/"category"/"kind of speech" concept, don't use that literal name — this codebase uses `stance` instead (kept consistent on the paired SQLAlchemy column and dataclass too, to avoid a translation layer at the ORM boundary), with `register` reserved for user-facing vocabulary only (CLI flag `--register`, display column), mirroring the existing `--type` → `memory_type` precedent (`type` collides with Python's builtin the same way). Before naming any new Pydantic field, a quick sanity check — define a throwaway `class T(BaseModel): <name>: str | None = None` and watch for a `UserWarning` — catches this class of collision (also applies to `copy`, `dict`, `json`, `schema`, and other `BaseModel`/`ABCMeta` method names) before it reaches a migration or a real model.

### `mentions` relationship edges inherit the same per-worktree "last indexed wins" property as `Note.content_hash`

**Date:** 2026-07-23 · **Source:** PR #29 (`relationship-graph-traversal-9xnnu9`), `_sync_note_mentions` in `src/wakil/app/workspace_service.py`

`index_notes` extracts `[[wikilink]]`s from each note body it sees on disk into `mentions` `Relationship` rows (ADR 0006 Phase 1), and `_sync_note_mentions` deliberately leaves notes absent from the current checkout's `present_paths` untouched — this is correct and already documented in its own docstring, so a note that only exists on another branch doesn't have its edges wrongly pruned. But the corollary is the same underlying property as the "Git worktrees fix the ingest lock race, but wakil's workspace identity isn't worktree-aware" entry above, one level down: a note's `mentions` edges reflect whichever worktree last ran `index_notes` against it, exactly like `Note.content_hash`/frontmatter already only reflect the last-indexed worktree's version of that file's content. Two linked worktrees on divergent branches, sharing one `state_root`-keyed database, will have the more-recently-indexed worktree's link graph for a shared note "win" — not a new bug, just the existing single-writer-view property extending into the new relationship table now that one exists.

This isn't a regression PR #29 introduced (the `Note` row's own `content_hash`/frontmatter already had this property before any `Relationship` table existed) and there's no reported symptom yet — recorded here so that if `wakil relationships`/backlink output ever looks stale or one-sided for a note actively edited across two worktrees, the first thing to check is which worktree most recently ran `index_notes` against that note, not the traversal query itself.

### An adversarial-review subagent's isolation checkouts left the working tree in detached HEAD

**Date:** 2026-07-23 · **Source:** PR #27 (`feat/memory-register-phase-2`), round-2 adversarial review

A review subagent verified "each of the 4 commits builds/passes standalone" by running `git log`/`git show --stat`/`git checkout <sha>` experiments on the branch directly (its own suggested verification method), but never checked back out to the branch tip afterward. The next actor (the main session, continuing on the same checkout) resumed work believing it was still on `feat/memory-register-phase-2`, and `git branch --show-current` returned empty — a plain `git status`/`git diff` looked deceptively normal (a detached HEAD at an ancestor commit still shows real, correctly-tracked files, just an older subset of them), and a test file that should have had four fixture memories and a `--register` test appeared to have only three and none, which read at first glance like a mysteriously reverted change rather than "wrong commit checked out entirely." Caught by noticing `git branch --show-current` was empty and `git log --oneline -1 -- <file>` pointed at an old commit, not by anything failing loudly.

No harm resulted here — the actual branch pointer (`feat/memory-register-phase-2`) was never moved, only the working tree's checkout target, so `git checkout <branch-name>` recovered instantly with the later uncommitted edit carried forward intact. But the lesson generalizes: any agent (subagent or main session) that checks out a specific commit SHA to test/inspect it in isolation must checkout back to the branch name before finishing or handing off — `git checkout <sha>` for verification should be immediately followed by `git checkout <branch>`, and the safer alternative when only running tests against an old commit is a disposable `git worktree add` rather than moving the shared checkout's HEAD at all.

### Neither `output_config.effort` nor `thinking: {type: "disabled"}` is a safe substitute for bounding thinking-token cost

**Date:** 2026-07-25 · **Source:** ADR 0015 (Decision 2, Step A), live experiment against `claude-opus-4-8`

`docs/DEVELOPMENT.md`'s entry on `thinking.budget_tokens` being unavailable on `claude-opus-4-8`+ names `output_config.effort` and `thinking: {type: "disabled"}` as "the two levers that are actually available" — true, but neither turned out to fix the truncation they were tried against. Replaying the entity-revision call that originally truncated (10 entities, `max_tokens=16384`) against the live API: `effort="low"` changed thinking-token consumption from 10,168 to 10,111 tokens — a 0.6% difference, not a meaningful lever. `thinking: {type: "disabled"}` avoided the token-budget crunch entirely (8,589/16,384 output tokens used) but the model narrated its reasoning in plain prose directly in the response text ("Let me work through each...") instead of emitting the required JSON schema — an unparseable, unusable response despite having budget to spare, not a quality-vs-cost tradeoff.

Before reaching for either lever to fix a truncation/token-budget problem on a structured-output call in this codebase, verify empirically against the actual failing request first — a plausible-sounding mitigation ("just lower effort," "just turn off thinking") can fail silently in a way a docs page or type stub can't reveal, and `thinking: {type: "disabled"}` in particular can trade a detectable failure (truncation, caught by `ModelTruncatedError`) for an equally-broken but differently-shaped one (a `ValidationError` from prose where JSON should be).

### A fully implemented, tested, and live-validated change was opened in a PR without its own commit — verify `git status`/`git diff` immediately before opening, don't rely on memory

**Date:** 2026-07-25 · **Source:** PR #32 (`feat/entity-revision-caching-and-relevance`), adversarial PR review

ADR 0015's Decision 2 (Step B: bisect a truncating entity-revision batch) was implemented, unit-tested (479 passed), and live-validated against the real originally-failing transcript — but the working tree was never `git commit`ed before a `wakil enrich` live-validation run and, separately, a PR was opened (`gh pr create`) claiming the change as shipped. In between, the shared working directory got checked out to `main` for an unrelated commit (another actor, same checkout) — something in that sequence stashed the uncommitted Step B changes (`git stash list` showed `wip: entity-revision-caching-and-relevance (untouched by ADR 0016 commit)`, so the stash itself was made carefully, but nothing surfaced it as still-pending work afterward). The PR was opened against the branch's last *committed* state — one commit behind — so the PR's description described real, tested code that was not actually present in the diff. An adversarial PR-review pass caught the discrepancy immediately by grepping the diff for the claimed function names and finding nothing.

Nothing was lost — `git stash list` still had it, `git stash pop` recovered it cleanly, and re-running the test suite confirmed it matched what had been validated. But the near-miss is the lesson: **run `git status` and `git diff <base>...<branch>` immediately before `gh pr create`, not from memory of having committed** — a shared working tree can be modified by activity outside the current tool-call history (another session, another terminal, the user's own git commands) between when work is done and when a PR is opened, and neither `pytest` passing nor a live validation run having happened proves the code that was tested is the code that's committed.

### Citing a prior ADR "from memory" can cite an earlier draft that no longer exists in the accepted document

**Date:** 2026-07-26 · **Source:** ADR 0017 (`docs/adr-0017-compiled-truth-size-management` branch), round-1 adversarial review

ADR 0017's first draft justified a new mechanism as "revives ADR 0016's originally-deferred 'passive nudge' idea (its Decision section's \"Passive nudge from `wakil enrich`\" bullet)" — citing specific prose as if quoting the current, accepted ADR 0016. `grep -i nudge` against the real file returned nothing. The section had genuinely existed, but only in an early, pre-narrowing draft of ADR 0016 (read and reasoned about extensively earlier in the same session, before that ADR went through its own adversarial-review narrowing and had large parts of its Decision section rewritten). The citing session's memory of "I read this in ADR 0016" was accurate for a draft that no longer existed by the time it was cited — the current document and the remembered one had diverged, silently, without the citing act re-checking which one it was actually pointing at.

This is a sharper version of the "verify git state before asserting" family of gotchas already in this file, specific to documents that get heavily revised: an ADR that has gone through adversarial-review narrowing (0015, 0016, and now 0017 itself) is not a stable citation target from memory, even within the same session that did the revising — the reviser's own recollection of "what an earlier version said" can outlive the edit that removed it. Before citing specific prose from a prior ADR as precedent or justification (not just its general conclusion), `grep` the current file for the exact claim first, the same discipline already applied to citing code behavior.

### A retry-loop distinction that reads reasonable in a design doc can be a genuine infinite loop in a deterministic mock

**Date:** 2026-07-26 · **Source:** ADR 0017 implementation (`feat/compiled-truth-size-management` branch), `src/wakil/cli/main.py`

ADR 0017's edit-flow spec distinguished two recovery targets for a rejected hand-edit in `wakil entities compile`'s interactive menu: a cancelled edit (`click.edit()` returns `None`) goes back to the top-level a/e/f/c menu, while an edit-specific problem (empty result, a Timeline-heading collision) goes "back to the edit choice" — read literally, an automatic re-invocation of `click.edit()` rather than re-prompting the menu. Implemented exactly that way, `uv run pytest` hung indefinitely (killed after 3+ minutes) on the pre-existing `test_entities_compile_over_target_edit_rejects_empty_edit` test — its mock (`monkeypatch.setattr("click.edit", lambda text=None, **kwargs: "   \n")`) always returns the same whitespace-only string, so the auto-retry loop called it, rejected it, and called it again forever, with no bound and no path out short of the mock eventually returning something valid (which it never would, being a fixed lambda). Isolated by running just the affected test module with a hard `timeout` wrapper rather than re-running the full suite blind a second time, which had also hung without pointing at a specific test.

The bug isn't specific to the mock — a real misconfigured `$EDITOR`, or a user who saves the same mistake twice, hits the identical unbounded loop in production. The fix was to drop the literal "auto-retry the editor" reading entirely: every reject-and-retry case (cancelled, empty, heading-collision, still-over-target) now returns to the same explicit top-level menu, which the user must actively respond to — including an always-available "c" to cancel — so the loop cannot run without a human choosing to continue it. One extra keystroke per rejected edit, in exchange for removing a real hang.

The general lesson: a design document's wording for "try again" behavior — "return to X" vs. "return to Y" — needs to be checked against what a *deterministic, unchanging* retry input does, not just against the happy path where a human eventually varies their input. If a retry loop's exit condition depends on the *content* of a value that can plausibly stay constant across calls (a stubbed test double, a broken external tool, a confused but persistent user), the loop needs an exit that doesn't depend on that content changing — an explicit choice the caller must make, a retry count, or both. This wasn't caught by two rounds of adversarial ADR review reading the design in prose; it was only caught by writing the actual code and running the actual test suite.

### A visible-degradation fix in one function can feed a hard-stop invariant gate in another, silently escalating a warning into a total abort

**Date:** 2026-07-27 · **Source:** PR #49 (`fix/issue-40-index-source-visibility`), commit `0c4f238`, independent verification of an earlier commit on the same PR

An earlier commit on this branch made `_build_stub_entities` (`src/wakil/app/ingest_service.py`) append a warning — rather than silently `continue`ing — for any create-resolution whose type has no canonical directory (e.g. the `index` schema, `directory: null`), and added `entity-resolve/SKILL.md` guidance actively steering the model toward proposing exactly that shape for index/list-like sources. Neither change touched `validate_proposal()`, which independently walks the same `proposal.entity_resolutions` list and raises a hard `ProposalIssue` for any pending `action == "create"` whose type has no schema *or* no schema directory. The CLI (`src/wakil/cli/main.py`) aborts the entire enrichment — nothing written at all, not even the source's own unrelated `proposed_note` — if `validate_proposal` returns any issue. So the "make the skip visible" fix, combined with the new guidance making the model *more* likely to hit exactly that skip, turned a silent per-entity drop into a total-apply hard failure for the sources issue #40 was about — worse than the original bug, and not caught until an independent verification pass traced the shared list through both functions.

The fix (this commit) has `_build_stub_entities` drop the resolution from `proposal.entity_resolutions` once it has recorded the warning for a schema-present-but-no-directory type, so `validate_proposal`'s create-scanning loop never sees it — while leaving a genuinely unknown type (no schema at all) in the list so that hard stop still fires correctly. The general lesson: when a fix makes some previously-silent path *visible* (a warning, a log line, a returned-but-not-raised value), check whether anything downstream treats that same input as an invariant violation rather than a soft signal — a shared mutable list (or any other value multiple independently-modified functions iterate) is exactly the surface where "we made X visible over here" and "we hard-stop on X over there" can combine into a worse regression than either change alone.

### `yaml.safe_dump` quotes a plain string that looks like a date but leaves a real `date` object unquoted — a str/date type mismatch shows up as inconsistent frontmatter, not an error

**Date:** 2026-07-27 · **Source:** issue #78, `fix/issue-78-updated-date-quoting` branch, `_merge_entity_note` in `src/wakil/app/ingest_service.py`

`_merge_entity_note` set `metadata["updated"]` to `today`, a plain `str` (`datetime.now(UTC).date().isoformat()`). `yaml.safe_dump` quotes any plain string that matches YAML's implicit date pattern, to keep it from being re-parsed as a date scalar on load — so `updated:` always rendered as `updated: '2026-07-16'`. `metadata["created"]`, by contrast, is never freshly assigned on a merge; it survives round-tripped from `frontmatter_lib.loads()`, where PyYAML's implicit resolver had already parsed the original unquoted `created: 2026-01-15` into a real `datetime.date` object — which `safe_dump` renders unquoted. Two frontmatter fields with identical date semantics came out formatted differently, silently, with no error or test failure pointing at the cause.

The fix was `date.fromisoformat(today)` at the assignment site rather than a string. The general lesson: when a value is dumped via `yaml.safe_dump` alongside other values of the same conceptual type (here, "a date"), check that they share the same *Python type*, not just the same string representation — `str` vs. `date`/`datetime` is invisible in the source until it hits the YAML dumper's implicit-type resolver, and it silently produces cosmetically inconsistent output rather than a crash. This applies to any future frontmatter field meant to look like an existing date-typed field.

### Guidance added to the wrong DAG-stage skill file cannot fix a bug in a different stage, no matter how it's worded

**Date:** 2026-07-27 · **Source:** issue #94 (`fix/issue-94-reflection-shape-in-extraction-skill` branch), issue #75/PR #83

A hand-authored, multi-date personal reflection (discussing 1-2 external works in passing), captured as `source_type: "text"`, repeatedly failed to land as a journal entry across four confirmation rounds — it split into empty stub entities for the referenced works instead, with `journal/` staying empty every time. Two separate guidance attempts (#75, PR #83) added "resolve first-person reflective content as its own journal entry" prose to `entity-resolve/SKILL.md` and produced zero measurable behavior change, because that file governs the *second* model call in `prepare_enrichment`'s DAG (`_run_entity_resolution`) — its job is create/update/skip for entities the source *touches*, never the type of the source's *own* `proposed_note`. That decision is made entirely by the *first* call (`_run_extraction`, using `skills/<source_type>/SKILL.md` — `text/SKILL.md` for this shape), which had zero guidance recognizing a first-person reflection as a shape at all. No wording change to `entity-resolve/SKILL.md`, however correct in isolation, could ever have moved `proposed_note`'s type — it answers a question a different, already-completed model call had settled first.

Before adding or rewording skill guidance to fix an ingest-pipeline behavior, identify which DAG stage (and which skill file) actually owns the specific output field in question — `prepare_enrichment`'s own node comments (`# DAG node 1: extraction judgment`, `# DAG node 2: entity resolution`) name the split — rather than assuming the most semantically-relevant-sounding skill file is the one to edit. A guidance fix that reads correctly and still produces zero behavior change across repeated confirmation rounds is itself the signal to check *which* skill file is even in the causal path, before trying yet another wording of the same prose in the same wrong place.

### Two rounds of a code-level "insurance" heuristic for personal-reflection shape each fixed one failure mode by introducing another — the approach itself, not the tuning, was the problem

**Date:** 2026-07-27 · **Source:** issue #94 (`fix/issue-94-reflection-shape-in-extraction-skill` branch), commits `af27e14` and `78249ac`, later reverted

Alongside the `text/SKILL.md` guidance fix above (the part that actually addressed the bug), the same PR added a conservative, non-binding code-level backstop — `_looks_like_personal_reflection` in `src/wakil/app/ingest_service.py` — meant to catch cases where the guidance might not be followed: multiple dated markdown-header sections, high first-person pronoun density, and no single dominant recurring subject word across sections. Independent verification round 1 found it silently failed to fire on the literal minimum reported shape (a two-section reflection sharing one ordinary word like "outside," which tripped the "dominant recurring subject" veto meant to exclude build logs). The follow-up fix replaced that unilateral veto with a second, weighed signal — `_has_reflective_language`, a phrase-based regex for introspective wording like "I think," "I feel," "I need to" — reasoning that a build log or meeting transcript would lack that language even though it shares the recurring-subject and pronoun-density signals. Independent verification round 2 found this introduced a new, more concerning failure: it now false-positived on an ordinary meeting transcript using nothing but common workplace phrasing ("I think we should tackle the billing migration first," "I need to fix this bug"), because that phrasing is lexically indistinguishable from genuine reflective language.

Each round's fix correctly patched the exact case the previous verification found, and each round's fix introduced a different failure mode in the same class (false negative, then false positive) rather than converging toward a reliable check. This is the signature of a heuristic problem, not a tuning problem: inferring authorial intent ("is this a personal reflection") from surface lexical patterns (dated headers, pronoun density, phrase regexes) doesn't have a deterministic decision boundary — any fixed threshold or keyword list is either too loose (catches ordinary prose that happens to share the surface pattern) or too strict (misses real instances that don't). The heuristic and its warning mechanism were removed entirely rather than tuned a third time; the project's own simplicity principle (`CLAUDE.md`: "keep the implementation simple unless added complexity has a clear and self-evident impact on the target use case") favors relying on the correctly-targeted guidance fix above alone. The general lesson: when a second consecutive independent verification pass finds a *different* failure mode in a natural-language-pattern-matching heuristic than the first pass did, that's a signal to question the approach itself, not to add a third signal on top of the first two — some "infer semantic intent from text" judgments belong to the model with well-placed guidance, not to layered regex backstops in code.

### `hatch-vcs` release tags must be PEP 440-parseable after the `v` prefix — a hyphenated suffix like `-verify-test` fails the build, not just a warning

**Date:** 2026-07-27 · **Source:** `release/versioning-hatch-vcs` branch, verification of `[tool.hatch.version] source = "vcs"` in `pyproject.toml`

Testing the new git-tag-derived versioning by tagging a commit `v0.1.0-verify-test` and running `uv build` produced a `UserWarning: tag 'v0.1.0-verify-test' version 'v0.1.0-verify-test' could not be parsed`, followed by a hard failure (`ValueError: Error getting the version from source \`vcs\`: Can't parse version from tag ...`) — the build aborts entirely, it does not silently fall back to a distance-based dev version. The regex hatch-vcs applies after stripping the optional `v`/`V` prefix (`^\d+(?:\.\d+){0,2}[^\+]*$` roughly) does not accept an arbitrary `-word-word` suffix; PEP 440 pre/post/dev/local segments have specific forms (`v0.1.0rc1`, `v0.1.0.post1`, `v0.1.0+test`, etc.), and a free-text hyphenated suffix isn't one of them. A clean tag like `v9.8.7` parses and builds correctly (producing e.g. `wakil-9.8.8.dev0+g<hash>.d<date>` when the tree is dirty and ahead of the tag).

Future release tags for this repo must be plain PEP 440-compatible strings (`vX.Y.Z`, optionally `vX.Y.ZrcN` for pre-releases) — do not append descriptive hyphenated suffixes to a release tag, or `uv build`/`hatch-vcs` will fail outright rather than degrade gracefully.

### `git-cliff`'s `commit.message` template variable is the full commit body, not just the subject line

**Date:** 2026-07-28 · **Source:** `release/changelog-and-workflow` branch, first real `workflow_dispatch` dry run of `.github/workflows/release.yml`

`cliff.toml`'s changelog body template originally rendered `{{ commit.message | upper_first }}` per commit. A dry run of the actual release workflow (not just a config read-through) showed every entry included the full multi-paragraph commit body verbatim — including `Co-Authored-By:`/`Claude-Session:` trailer lines — because `commit.message` in git-cliff is the entire raw commit message, not the parsed subject line, unless explicitly trimmed. `commit_parsers` matching against `^.{0,3}feat(...)：` only determines *grouping*; it has no effect on what text gets rendered.

Fixed by rendering `{{ commit.message | split(pat="\n") | first | trim | upper_first }}` instead — Tera's `split`/`first` filters, not a `commit_preprocessors` regex, since the full body is still useful context available elsewhere if ever needed and this only affects the changelog's own rendering. Also needed `{% for commit in commits -%}` (a trailing `-` trim marker) to close a blank-line-per-entry gap the split introduced, since a plain `{% for %}` tag leaves its own newline in the rendered output.

The general lesson: git-cliff's default template variables reflect the *raw* commit data, not an assumed "changelog-appropriate" projection of it — verify generated *output* against real multi-line commits (this repo's commits routinely carry bodies and trailers) before trusting a template that looks correct by inspection.
