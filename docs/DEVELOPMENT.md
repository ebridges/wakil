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

### A skill's `references/*.md` files are never loaded into the model's context
**Source:** `fix/eval-suite-flakiness-and-skill-gaps` branch, live-eval triage session, 2026-07-21

`load_skill()` (`src/wakil/llm/skill_loader.py`) builds `skill.body` from `SKILL.md` alone — nothing inlines a skill's `references/*.md` files, even though several SKILL.md files (`source-ingestion`, `skill-authoring`) point at them for what reads as load-bearing judgment ("see `references/source-types.md` for the per-source-type judgment"). This is true both in `wakil enrich`'s real pipeline (`build_system_prompt`) and the eval harness (`run_scenario`) — a human reading the skill file would follow the pointer and find the guidance; the model never does, regardless of how clearly SKILL.md cites the file. Two separate live-eval failures (a fabricated YouTube-transcription workaround, a promotion checklist the model never applied) traced to this exact cause. When a SKILL.md cites a `references/` file for guidance that actually needs to affect model behavior (not just serve as a longer human-readable writeup), pull the load-bearing part into `SKILL.md`'s own body — `references/` can still carry the fuller version, but the file itself must not be the only place the instruction lives.

### Check whether a skill's own procedure step order can produce the bug it's trying to prevent
**Source:** `fix/eval-suite-flakiness-and-skill-gaps` branch, `note-routing/SKILL.md`, 2026-07-21

`note-routing`'s decision tree had "generate the filename" as Step 5 and "surface ambiguity, don't pick" as Step 6 — so a model following the numbered steps in order committed to a filename before ever reaching the ambiguity check the later step was supposed to gate. The instruction to "surface ambiguity" was correct in isolation; the bug was purely in where it sat relative to the step it needed to precede. When a skill's procedure is a numbered sequence and a later step is meant to be a conditional gate on an earlier one, check that the gate actually comes first — a correct rule stated too late in the list doesn't prevent the thing it names.

