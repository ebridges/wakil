---
title: Development Patterns
status: living
audience: wakil design
---

# Development Patterns

## About

Recurring dev conventions worth generalizing to future, different work — not
a changelog and not a place for one-off fixes. An entry only belongs here if
the pattern should apply beyond the change that established it. Most work
sessions should add nothing; see the `development-docs` skill
(`.claude/skills/development-docs/SKILL.md`) for the judgment process that maintains
this file. Every entry cites a concrete source (commit SHA, PR #, or session
detail) for traceability.

### Split error handling around a git-landing step

**Source:** PR #15 (`worktree-git-integration` branch), `src/wakil/cli/main.py`

When a command lands on a branch (`prepare_landing`) before doing risked work (e.g. `apply_capture`/`apply_enrichment`), catch the landing call's `GitServiceError` in its own `try` block, separate from the subsequent operation's errors. On any failure in the operation after a successful landing, call `abandon_landing(config, landing)` so the session doesn't sit on the throwaway ingest branch. `enrich` established this shape first (commit `f5a4ed1`); `_run_ingest` originally wrapped `prepare_landing` and `apply_capture` in one combined `try` block, and was split to match `enrich` in commit `a14af48`.

The destination has since changed: `abandon_landing`/`land_ingestion` return to the repository's **default branch**, not to whatever branch the shell happened to be on when the command started. That value used to be frozen in `LandingContext.original_branch` and replayed minutes later (unbounded, for MCP's prepare/apply split), which is how `enrich` came to "return to" a stale, unrelated branch — issue #181. `ensure_clean_for_branch` already guarantees a clean tree at prepare time, so the incidental branch carries no information worth preserving.

Two exceptions, both cases where returning is the wrong move:

- **The commit itself failed** (#171). `git add` may already have succeeded — the common case is a signing prompt timing out between the `add` and the `commit` — so switching away strands staged work on a branch the caller is no longer on. Stay put.
- **HEAD drifted** (`BranchDriftError`, #182/#195). Drift means *another process owns this working tree*. Returning would `git switch` out from under it, which is precisely the clobber the drift detection exists to prevent — so the drift case re-raises without returning, while ordinary push/`gh` failures still return.

The rule is "return once the work is durable in git and the tree is still ours; stay put otherwise."

The stay-put rule is scoped to the *call*, not to the timeout: `land_ingestion` catches `GitError`, so a rejecting `pre-commit` hook or a signing failure stays put too. That is deliberate — the staged work is equally stranded either way — but only a *killed* commit gets the lock cleanup and recovery text below.

Because staying put leaves HEAD somewhere the caller didn't put it, the failure message has to *say so* — both callers name the branch and note that changes may be staged there. Silence was survivable when the session was returned to its own branch; it isn't now. Escape that message: `str(exc)` embeds the hand-finish command, whose commit body is model-generated and routinely contains `[[wikilinks]]` that Rich reads as style tags and deletes — printing a command that looks right and isn't.

A killed `git commit` also needs cleaning up after. `subprocess.run` SIGKILLs the child, and git holds `.git/index.lock` for the whole commit — including the signing prompt — so the corpse leaves the lock behind and *every* later git write in the repo fails until someone deletes a file by hand. Recovery advice that assumes the repo is still usable is wrong unless you clear that first; see `_recover_from_killed_commit`.

Note: as of this writing PR #15 is still an open draft (unmerged into `main`); the `abandon_landing` half of this pattern applies once/if that branch lands.

### Run the live eval before treating new SKILL.md guidance as done

**Source:** PR #20 (`docs/skill-guidance-for-linked-context` branch), `src/wakil/skills/entity-enrichment/SKILL.md`

Adding a new rule to a skill's SKILL.md and a matching `eval.json` scenario for it isn't finished until `uv run pytest -m eval -k <scenario id>` (a real model call, per `docs/adr/0004`) actually passes — prose that reads correct to a human reviewer can still be misapplied by the model in a way only a live run surfaces. Here, a new rule said an explicit `@file:` reference should be treated as "high-confidence" for back-linking; the model read that confidence as also covering content-worthiness and appended a Timeline entry for a mention the source said had nothing new to report, failing the eval's rubric. The fix was one clarifying sentence separating the link decision from the content decision — but the gap was invisible without actually running the eval. When adding or editing skill guidance, write (or extend) its `eval.json` scenario and run it live before considering the change done, not just at merge/CI time.

### A skill's `references/*.md` files are never loaded into the model's context

**Source:** `fix/eval-suite-flakiness-and-skill-gaps` branch, live-eval triage session, 2026-07-21

`load_skill()` (`src/wakil/llm/skill_loader.py`) builds `skill.body` from `SKILL.md` alone — nothing inlines a skill's `references/*.md` files, even though several SKILL.md files (`source-ingestion`, `skill-authoring`) point at them for what reads as load-bearing judgment ("see `references/source-types.md` for the per-source-type judgment"). This is true both in `wakil enrich`'s real pipeline (`build_system_prompt`) and the eval harness (`run_scenario`) — a human reading the skill file would follow the pointer and find the guidance; the model never does, regardless of how clearly SKILL.md cites the file. Two separate live-eval failures (a fabricated YouTube-transcription workaround, a promotion checklist the model never applied) traced to this exact cause. When a SKILL.md cites a `references/` file for guidance that actually needs to affect model behavior (not just serve as a longer human-readable writeup), pull the load-bearing part into `SKILL.md`'s own body — `references/` can still carry the fuller version, but the file itself must not be the only place the instruction lives.

### Check whether a skill's own procedure step order can produce the bug it's trying to prevent

**Source:** `fix/eval-suite-flakiness-and-skill-gaps` branch, `note-routing/SKILL.md`, 2026-07-21

`note-routing`'s decision tree had "generate the filename" as Step 5 and "surface ambiguity, don't pick" as Step 6 — so a model following the numbered steps in order committed to a filename before ever reaching the ambiguity check the later step was supposed to gate. The instruction to "surface ambiguity" was correct in isolation; the bug was purely in where it sat relative to the step it needed to precede. When a skill's procedure is a numbered sequence and a later step is meant to be a conditional gate on an earlier one, check that the gate actually comes first — a correct rule stated too late in the list doesn't prevent the thing it names.

### Print diagnostic detail as it happens, not deferred to a final summary

**Source:** `fix/eval-suite-flakiness-and-skill-gaps` branch, `scripts/classify_eval_failures.py` and `uv run pytest -m eval -v` runs, 2026-07-21/22

Two separate long-running eval scripts in this session lost their most important output to the same mistake: accumulating results in memory and only printing the useful summary (pytest's failure list, this script's per-item failure reasons) at the very end of the run. `scripts/classify_eval_failures.py` hit a live-model API usage cap partway through a 10-scenario classification pass and crashed — the log had per-scenario pass/fail counts but none of the actual failing rubric items or grader reasons, because those were only assembled into the final `CLASSIFICATION TABLE` the crash never reached. Fixed by printing each individual grading result (pass/fail, failed item, grader reason) immediately as it happens, keeping the end-of-run table as a convenience recap rather than the only place the detail lives. For any script making a sequence of live model calls with real cost/failure risk, print the thing you'd actually need to read *at the point each result arrives*, not after the loop — a partial run should still be a useful run.

### Zip a vendored data file, document its source in a sibling `.SOURCE.md`

**Source:** PR #21 (`refactor/candidate-notes-stopword-filtering` branch), `src/wakil/app/data/`

When vendoring a third-party data file (a word list, a static lookup table) into the repo, zip it rather than committing plain text — `common_words.zip` is 17KB against the original 34KB `common_words.txt`, decompressed once at import time via the stdlib `zipfile` module, not re-read per call. Because a zip's contents aren't reviewable in a plain `git diff`, add a sibling `<name>.SOURCE.md` documenting where the data came from, its license, and how to regenerate/refresh it — `common_words.SOURCE.md` is the template to copy for the next one.

### A full live-model eval-suite run has a high baseline failure rate — don't read it as a regression signal

**Source:** session comparing `uv run pytest -m eval -q` across PRs #21-#24 and `main`, 2026-07-21

Running the complete `-m eval` suite (all skill scenarios, not a single targeted scenario) produced roughly 45-53% failures on every branch tested, including ~16-17 of 47 scenarios that failed identically across branches with zero code overlap (SCHEMA.md removal, capture-time model calls, candidate-filtering, related-notes search — none of which share a code path). Every unique-to-one-branch failure spot-checked was the same character of failure: a subjective "did the model stay strictly within its stated skill boundary" or "did it avoid inventing a disambiguating detail" rubric item, not a functional break. This matches `docs/adr/0004`'s own rationale for excluding these from the required CI gate. Before treating a full-suite eval run's failures as evidence of a regression, compare against a same-day `main` baseline (or re-run the specific failing scenario in isolation) rather than reading the raw failure count at face value — a targeted, single-scenario eval run (e.g. verifying one new rubric item after a SKILL.md wording change) is a much more reliable signal than a full-suite sweep.

### An ADR's `status: accepted` means the decision was made, not that it was implemented

**Source:** `docs/relationship-graph-traversal-proposal.md`, 2026-07-23

ADR 0006 ("Backlinks as a Live Query Over a Widened Relationship Table") is `status: accepted` and its schema/migration exist, but grepping the real application code turned up exactly one place a `Relationship` row is ever constructed (`ingest_service.py`'s `apply_enrichment`) — and it only ever writes `subject_memory_id`/`object_memory_id`. The note-note columns the ADR is about (`subject_note_id`/`object_note_id`) are populated nowhere in the real pipeline, exercised only by one hand-constructed unit test roundtrip. This wasn't caught until a downstream consumer (a kb-side skill assuming backlinks were already automatic) went looking for the actual write path. Before citing an accepted ADR's decision as current, working behavior in a new design doc, a docstring, or downstream documentation, grep for where it's actually implemented — `status: accepted` records that a decision was made, not that every consequence of it landed in code.

### Diarized transcript formats each get their own `parse_<x>_transcript()` sharing one merge helper

**Source:** `fix/json-transcript-diarization` branch, `src/wakil/app/ingest_service.py`, 2026-07-25

A plain-JSON transcript export (`{"segments": [{"speaker": "Speaker 1", "text": ..., "start": ...}]}`) looked like the same shape as an Apple `.whisper` zip archive's `metadata.json` (`{"transcripts": [{"speaker": {"name": ...}, ...}]}`) but wasn't — different container (zip vs. plain file), different top-level key (`transcripts` vs. `segments`), different speaker shape (object vs. flat string) — so it fell through `prepare_capture`'s generic text branch and captured as a raw, undiarized JSON blob instead of dialogue. The fix factored the actual merge logic (consecutive-same-speaker turns, filler-word stripping) out of `parse_whisper_transcript` into a shared `_dialogue_from_segments(segments, speaker_of)` helper, then added `parse_json_transcript()` as a second, format-specific caller. The next new diarized export shape (a different ASR tool's JSON, an SRT-with-speaker-labels variant, etc.) should follow the same shape: a small `parse_<x>_transcript()` that extracts its own `segments` list and a `speaker_of` callable, gated on file suffix/content in `prepare_capture`, reusing `_dialogue_from_segments` rather than re-implementing the merge.

### Anthropic prompt caching needs an identical prefix through `system` too, not just a matching shared block

**Established:** 2026-07-25 · **Source:** commit 6b64a5a, `src/wakil/llm/client.py`, `ModelClient.complete()`'s `cacheable_prefix` param

Marking a block with `cache_control` only produces a cache hit if everything before it in the request — `system` included — is byte-identical to a previous call within the TTL. `build_system_prompt` (`skill_loader.py`) bakes each DAG stage's skill body + JSON schema into `system`, so the extraction/resolution/revision calls in one `wakil enrich` run never share a cache lineage even though they all send the same source document — their `system` differs first, breaking the prefix chain before it ever reaches the shared content. `cacheable_prefix` on `ModelClient.complete()` therefore only pays off *within* one call type (retries, and any future batched sub-calls of the same call), never *across* call types, unless `system` itself is made call-invariant — which has its own cost (skill/schema content would move into the user message, a weaker instruction-following position). Before assuming a shared block will cache across two different call sites, check whether their `system` is actually identical first.

### `thinking.budget_tokens` doesn't exist on `claude-opus-4-8` and later — use `output_config.effort` or `thinking: {type: "disabled"}` instead

**Established:** 2026-07-25 · **Source:** `docs/adr/0015-relevance-gated-entities-and-truncation-driven-batching.md` (Decision 2, Step A), `src/wakil/llm/client.py`'s `DEFAULT_ANTHROPIC_MODEL`

`AnthropicClient.complete` hardcodes `thinking={"type": "adaptive"}` with no way to bound how much of `max_tokens` thinking consumes before output starts. The obvious lever — `thinking: {type: "enabled", budget_tokens: N}` — returns a 400 error on `claude-opus-4-8` (this codebase's `DEFAULT_ANTHROPIC_MODEL`) and every later model in the family (Opus 4.7+, Fable 5, Mythos 5), confirmed against Anthropic's live migration guide (platform.claude.com/docs/en/about-claude/models/migration-guide.md) and not just assumed. The two levers that are actually available on this model, confirmed against the installed SDK's type stubs (`anthropic` package, `types/output_config_param.py`, `types/thinking_config_disabled_param.py`): `output_config={"effort": "low"|"medium"|"high"|"xhigh"|"max"}` (compatible with adaptive thinking, currently never set — the call runs at whatever server-side default applies), and `thinking: {type: "disabled"}` (eliminates thinking-token cost entirely, at the cost of whatever quality adaptive thinking buys). Before adding a `budget_tokens` cap to any Anthropic call in this codebase, check the current model's actual capability — model-specific thinking/effort support changes across the Claude model lineage, and a docs page is one step removed from observed behavior, so verify with a live call before relying on either lever in production.

### An entity-revision candidate's `content` is one shared in-memory value across prompt, merge, and stale-guard — trace all three before changing what it holds

**Established:** 2026-07-25 · **Source:** ADR 0016 review (rejected "gated context-trimming" design), `src/wakil/app/ingest_service.py`

`_run_entity_updates` reads each candidate's target-note content exactly once into a `content` string (part of the `_EntityCandidate` tuple), and that same in-memory value is reused three separate times downstream: as the model prompt's input (`_revise_candidates`), as the merge's `old_content` (`_apply_entity_revisions`, `by_path.get(...)`), and as the comparison baseline for `apply_enrichment`'s stale-file guard (`update.old_content`, checked against a fresh disk read at apply time). Nothing re-reads the file between prepare and apply. An ADR proposing to trim that string for just the prompt (to cut context cost) initially claimed the merge step was unaffected because it "always re-reads the full file at apply time" — checked against the actual code, it doesn't; the claim was simply wrong, and acting on it would have silently broken the stale-guard (a trimmed string can never match a fresh full read), the merge (no Timeline heading left for `_TIMELINE_HEADING_RE` to find), and `_split_candidates_by_content_length`'s cost-balancing (which sizes candidates by `len(content)`) all at once, for exactly the notes the change was meant to help. Before changing what a shared value like this holds for one consumer, grep for every other place that same variable/field is read, not just the one motivating the change.

### Known issue: `_resolve_entity_slug` silently picks a winner on a cross-type slug collision

**Source:** PR #33 (`feat/entity-compile-pilot` branch), `src/wakil/app/ingest_service.py`, session discussion 2026-07-26

`_resolve_entity_slug(config, slug)` (backing `wakil entities compile SLUG`) searches every entity type's canonical directory, sorted alphabetically, for a file named `<slug>.md`, and returns the first match — its own docstring says "only one page per slug is expected to exist in practice," but nothing enforces that. If two entities of different types ever share a slug (e.g. a person and a company both slugifying to `edward-bridges`), the resolver silently returns whichever directory sorts first (`companies` before `people`, alphabetically) with no error and no indication a second match exists. The only mitigating factor is that the preview step shows the full resolved path before the user confirms — but nothing actively flags the ambiguity, so an inattentive confirm could compile the wrong entity. Not yet fixed. The intended fix, if picked up: keep the bare-slug ergonomics for the (overwhelmingly common) unambiguous case, but have `_resolve_entity_slug` detect a match in more than one directory and raise a clear error listing every match, directing the user to a full relative path instead of silently guessing — the same "surface ambiguity, don't pick" discipline `note-routing/SKILL.md` already applies to filename generation (see the entry above on step ordering).

### Adding a mandatory model call to the enrichment DAG means auditing every `FakeClient(...)` construction, not just bumping a default

**Established:** 2026-07-27 · **Source:** issue #70, `src/wakil/app/ingest_service.py`'s `_synthesize_stub_content`, `tests/unit/test_ingest_service.py`

`prepare_enrichment`'s DAG (extraction → entity-resolution → entity-updates → …) is exercised almost entirely through `FakeClient`, a fixed-length scripted-response queue with no notion of "this call is optional" — each test's list length has to exactly match however many real model calls that specific resolution shape triggers. Adding a new *unconditional* call partway through the DAG (here: synthesize content for every create-resolution's stub once it survives suppression) broke every test whose fixture happened to produce that condition, not just the ones that explicitly asserted a call count — including tests using the bare default `FakeClient()` and tests with an explicit 2-3-item list built for the DAG shape *before* the new call existed. There is no shortcut around this: bumping `FakeClient.__init__`'s default queue only covers bare `FakeClient()` callers; every explicit-list construction still has to be individually re-derived (does this resolution's create survive suppression? does an update-candidate's target file exist on disk?) and given one more scripted response, or the test fails opaquely with "FakeClient ran out of scripted responses" rather than a clear diff. When adding a new call to this (or any other) fixed-length-fake-backed DAG, budget time to grep every construction site of the fake client, not just its default, before trusting a green test run.

### Stacked same-file refactor PRs from a shared base conflict on sequential merge even when the functions they touch don't overlap

**Established:** 2026-07-30 · **Source:** PRs #109, #110, #111 (three independent function-level refactors of `src/wakil/app/ingest_service.py`, each branched from the same pre-refactor commit)

Three worktree-agent PRs each extracted helpers from a *different* function in `ingest_service.py` (`apply_enrichment`, `_build_stub_entities`, `validate_proposal`), with no line ranges in common. Merging the first two into `main` sequentially was a clean fast-forward, but the third (`validate_proposal`) then failed `gh pr merge` with a conflict, because git's diff anchors on the *text* around a change, not on which named function it belongs to — once `apply_enrichment`'s body was replaced by a call to its new helpers on `main`, the region immediately following the still-unrefactored (from that branch's point of view) `validate_proposal` no longer matched what the `validate_proposal` branch expected to find there, even though neither PR touched the other's actual function body. Rebasing the trailing branch onto the new `main` reproduces the same conflict (it's not merge-order-specific); resolving it requires re-deriving the intended final file — start from `main`'s current version of the file and manually re-apply just the extracted-function's diff, rather than trying to hand-resolve `<<<<<<<`/`>>>>>>>` markers in place, since the marked regions don't line up with either branch's actual intended change. When queuing multiple same-file refactor PRs from one audit, expect this on any PR after the first that touches that file, and budget for a manual reconciliation pass rather than a routine merge.

### MCP tools live in `mcp/tools.py` as plain functions; `mcp/server.py` wraps them in closures for FastMCP registration

**Established:** 2026-07-28 · **Source:** `docs/adr/0018-mcp-interface.md`, `src/wakil/mcp/tools.py`/`server.py`

FastMCP's `@mcp.tool()` decorator introspects the wrapped function's signature to build the tool's client-facing JSON schema — any parameter present on that function becomes something the MCP client is expected to supply. Every wakil MCP tool needs a `WorkspaceConfig` (and the write tools need the shared `ProposalCache`) that must stay fixed for the life of one `wakil mcp serve` process, not be re-supplied per call, so those can't be plain parameters on the decorated function. `mcp/tools.py` holds the actual logic as ordinary functions taking `config`/`cache` explicitly first, directly unit-testable with no running server (`tests/unit/test_mcp_tools.py` calls them with no MCP machinery at all); `mcp/server.py`'s `build_server(config)` builds one `ProposalCache`, then registers a thin closure per tool that captures `config`/`cache` and forwards only the remaining, client-facing parameters. The next new MCP tool should follow the same split — logic in `tools.py`, a same-named closure wrapper in `server.py` — rather than writing logic directly inside a `@mcp.tool()`-decorated function.

### A `FakeClient`'s strict queue order stops being reliable once two of its calls run on separate threads

**Established:** 2026-08-03 · **Source:** `docs/adr/0020-enrich-progress-visibility-concurrency-and-checkpointing.md` (Decision 2), `tests/unit/test_ingest_service.py`'s `FakeClient`

`prepare_enrichment`'s DAG nodes 3 and 4 (`_run_entity_updates`/`_synthesize_stub_content`) now run concurrently via a `ThreadPoolExecutor`, and both share the exact same contract (`EntityRevisionOutput`) and system prompt — indistinguishable to a scripted fake except by which one calls `complete()` first, which thread scheduling now decides rather than the DAG's own code order. Any test that scripted both calls into one `FakeClient([...])` queue and asserted something about a *specific* one of them (its prompt content, or a response tailored to one call but not the other) is flaky under this change, even though the test never mentions threads. `FakeClient` gained an optional `router` param — a list of `(prompt-substring, response)` pairs checked before the queue on every `complete()` call — so a test can pin a specific response to whichever call's prompt contains a target note path unique to it (revision targets and synthesis targets never overlap), while leaving the other call to draw from the plain queue. When adding concurrency between two DAG calls that share a contract, grep every existing scripted-fake test that exercises both calls in one run before trusting a green suite — a call-order assumption baked into fixture data doesn't show up as a diff anyone reviews.

### A back-compat check must be computed from a frozen copy of the old behavior, never from the current function

**Established:** 2026-08-09 · **Source:** PR #194 review rounds 2–3; `_LEGACY_BRACKET_TS_RE` and `PRE_179_MD_HASH` in `src/wakil/app/ingest_service.py` / `tests/unit/test_ingest_service.py`

When a change alters a stored, derived value — a content hash, a slug, a serialized key — the fallback that recognizes already-stored values has to reproduce *what the old code produced*, which means a frozen copy of the old logic, not a call to the current function. #194 wrote `legacy_text = clean_transcript(raw)` intending "what this hashed to before"; because the same PR had also changed `clean_transcript`'s regex, the fallback was inert for exactly the files it existed for. Two symptoms to watch for: the guard silently never fires, and any test of it passes tautologically because both sides of the comparison move together — pin the literal expected value (a digest, a string) computed against a real pre-change checkout, and say in a comment that it must not be regenerated from `HEAD`. Also enumerate every input path the changed function serves, not just the one in the PR's headline: the same fix initially covered `.md` only, while the regex change affected `.txt`/`.srt` captures identically.

### `EnrichmentCheckpoint` persists each `wakil enrich` DAG phase; a crash or failed `validate_proposal` is not always a full redo

**Established:** 2026-08-03 · **Source:** `docs/adr/0020-enrich-progress-visibility-concurrency-and-checkpointing.md` (Decision 3), `src/wakil/app/ingest_service.py`

Before assuming a killed/crashed `wakil enrich` run — or one that failed `validate_proposal` for a reason unrelated to any of its 4 model calls — always means starting over: check for `EnrichmentCheckpoint` rows for that source first (`sqlite3 <workspace>/wakil.db 'select phase, created_at from enrichment_checkpoints where source_id = N'`). Each of `prepare_enrichment`'s 4 phases (extraction, resolution, revision, synthesis) is checkpointed on clean completion, keyed by `sha256(source.content_hash | context_digest | model)` — a re-invocation with the same key skips straight past any phase with a matching row. Only clean completions are cached, never a model-call failure (extraction/resolution's `ModelContractError` branches skip the save so a transient failure stays retriable), and only `--force` or a successful `apply_enrichment` ever clears them — a declined preview or a failed `validate_proposal` leaves them in place deliberately. `_build_stub_entities` itself is never checkpointed (only its input, `entity_resolutions`, is) — it always re-runs fresh from whatever resolutions end up on the proposal, so it can't drift between a live and a resumed run.


### A tolerant read is for display; a decision needs a checked read

**Established:** 2026-08-08 · **Source:** issue #180/#181, the #195 review, `src/wakil/integrations/git.py`'s module docstring

`integrations/git.py` deliberately returns `None`/`[]` when a git read fails, so `wakil status` can never crash on a weird repo. That tolerance is right for rendering and wrong for deciding, and the two had been sharing one helper:

- `changed_files` failing → `ensure_clean_for_branch` reads "tree is clean" → wakil branches and commits **on top of the user's uncommitted work**.
- `branch_exists` failing → `_resume_source_branch` reads "no such branch" → cuts a **second** branch for a source that already had one, and the DB then disagrees with reality permanently.
- `resolve_default_branch` failing → `create_branch_from(base=None)` → cuts an "ingest" branch off **whatever unrelated branch is checked out**.

Each of those is a silent wrong action produced by a failed read, not by a wrong answer. The fix is a pair per read: keep the tolerant form for display (`changed_files`, `branch_exists`, `resolve_default_branch`, `inspect_git`) and add a checked sibling that raises (`status_lines`, `require_branch_exists`, `require_default_branch`, `current_branch`). Where a non-zero exit is itself a legitimate answer — "no such ref" — use a tri-state probe (`_run_git_probe`) so "absent" and "couldn't run git" stay distinguishable.

The generalizable rule: **before using a subprocess-wrapper's return value, ask what it returns when the subprocess fails, and whether that value is safe as an answer.** `None`-on-failure is a fine contract, but it means "no information", and code that treats it as "no" will act. This applies equally to `integrations/github.py` (a `gh` auth failure must not read as "there is no PR") and to any future wrapper of an external binary.

### Instants stay UTC; calendar dates are local

**Established:** 2026-08-07 · **Source:** issue #174, `workspace_today()` in `src/wakil/config/settings.py`

Every user-visible date in wakil used to come from `datetime.now(UTC).date()`. For a single-user local tool that is simply wrong: a capture run at 20:49 US-Eastern is already tomorrow in UTC, so four consecutive evening ingests in one session were all stamped a day ahead of the meetings they recorded — in the auto-generated `title`, in `captured`/`created`, and in the raw file's own date-prefixed filename.

The fix draws a line that any new date-producing code needs to stay on the right side of:

- **A calendar date a human reads is local.** `created`/`captured`/`retrieved` frontmatter, the date prefix on a raw capture's filename, Timeline entry headings, the `wakil/ingest/<date>-<slug>` branch name, and the "today" passed into a prompt. All of these go through `workspace_today(config)`, which honours the workspace's optional `timezone:` config key and otherwise uses the machine's local zone. Never call `datetime.now(UTC).date()` for one of these.
- **An instant used for ordering or arithmetic stays UTC.** `storage/schema.py`'s `utcnow()` still backs every `created_at`/`retrieved_at`/`last_seen_at` column. These are timestamps, not dates: `memory_service.retrieval_rank`'s age computation and every `ORDER BY` depend on a single monotonic reference, and making them local would break both across a DST boundary.

Two practical consequences worth knowing before writing the next date-touching test or helper. First, `tests/conftest.py` now pins `TZ=UTC` (with `time.tzset()`), because otherwise a test asserting a date in a filename passes in a UTC CI runner and fails on a US-Eastern laptop — pin or freeze the clock rather than asserting today's date directly. Second, `workspace_today` needs a `WorkspaceConfig`, and several enrichment helpers deep in `ingest_service.py` don't have one in scope; those take an explicit `today: str` parameter computed by a caller that does, rather than acquiring a config just to read a date. Prefer that over threading `config` through another layer.
