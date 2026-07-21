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

