---
title: Development Patterns
status: living
audience: wakil design
---

# Development Patterns

Recurring dev conventions worth generalizing to future, different work — not
a changelog and not a place for one-off fixes. An entry only belongs here if
the pattern should apply beyond the change that established it. Most work
sessions should add nothing; see the `development-docs` skill
(`.claude/skills/development-docs/SKILL.md`) for the judgment process that maintains
this file. Every entry cites a concrete source (commit SHA, PR #, or session
detail) for traceability.

### Split error handling around a git-landing step
**Source:** PR #15 (`worktree-git-integration` branch), `src/wakil/cli/main.py`

When a command lands on a branch (`prepare_landing`) before doing risked work (e.g. `apply_capture`/`apply_enrichment`), catch the landing call's `GitServiceError` in its own `try` block, separate from the subsequent operation's errors. On any failure in the operation after a successful landing, call `abandon_landing(config, landing)` so the worktree returns to its own branch instead of being left stranded on the throwaway ingest branch. `enrich` established this shape first (commit `f5a4ed1`); `_run_ingest` originally wrapped `prepare_landing` and `apply_capture` in one combined `try` block, and was split to match `enrich` in commit `a14af48`, whose message describes closing the gap this way: "so a race loss returns the session to its original branch via abandon_landing instead of stranding it on the throwaway ingest branch."

Note: as of this writing PR #15 is still an open draft (unmerged into `main`); this pattern applies once/if that branch lands.

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

