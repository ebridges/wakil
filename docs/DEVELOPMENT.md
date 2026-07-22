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

### Zip a vendored data file, document its source in a sibling `.SOURCE.md`
**Source:** PR #21 (`refactor/candidate-notes-stopword-filtering` branch), `src/wakil/app/data/`

When vendoring a third-party data file (a word list, a static lookup table) into the repo, zip it rather than committing plain text — `common_words.zip` is 17KB against the original 34KB `common_words.txt`, decompressed once at import time via the stdlib `zipfile` module, not re-read per call. Because a zip's contents aren't reviewable in a plain `git diff`, add a sibling `<name>.SOURCE.md` documenting where the data came from, its license, and how to regenerate/refresh it — `common_words.SOURCE.md` is the template to copy for the next one.

### A full live-model eval-suite run has a high baseline failure rate — don't read it as a regression signal
**Source:** session comparing `uv run pytest -m eval -q` across PRs #21-#24 and `main`, 2026-07-21

Running the complete `-m eval` suite (all skill scenarios, not a single targeted scenario) produced roughly 45-53% failures on every branch tested, including ~16-17 of 47 scenarios that failed identically across branches with zero code overlap (SCHEMA.md removal, capture-time model calls, candidate-filtering, related-notes search — none of which share a code path). Every unique-to-one-branch failure spot-checked was the same character of failure: a subjective "did the model stay strictly within its stated skill boundary" or "did it avoid inventing a disambiguating detail" rubric item, not a functional break. This matches `docs/adr/0004`'s own rationale for excluding these from the required CI gate. Before treating a full-suite eval run's failures as evidence of a regression, compare against a same-day `main` baseline (or re-run the specific failing scenario in isolation) rather than reading the raw failure count at face value — a targeted, single-scenario eval run (e.g. verifying one new rubric item after a SKILL.md wording change) is a much more reliable signal than a full-suite sweep.

