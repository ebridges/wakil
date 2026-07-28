---
title: Fast-capture review tempo — the PR satisfies review for skill-coordinated MCP flows
status: accepted
date: 2026-07-28
audience: wakil design
---

## Context

Designing the MCP interface (docs/adr/0018) surfaced a use case not
previously captured anywhere in `PROMPT.md`/`CLAUDE.md`: a user who is a
busy manager moving between meetings, who wants transcripts and notes into
the knowledge base fast enough that capturing them is worth the
interruption at all. Four separate confirmations per source — capture
preview, capture confirm, enrichment preview, enrichment confirm, whether
as CLI prompts or as four MCP round-trips a human reads in full each time —
is friction that works directly against that goal.

The natural next question is whether that friction is load-bearing. ADR
0008 decomposed ingestion specifically to avoid agent-decided sequencing —
"a step that must run, runs because code calls it, not because an agent
remembered to." Read literally as "a human must review every step before
every write," that would rule out any fast path. But that's not what ADR
0008 actually decided: its concern was the *predictability of what code
runs* (classify -> extract -> resolve -> enforce -> finalize, always, in
order, never agent-remembered), not a claim about when a human has to look
at the result. Every wakil-driven change already lands on a branch and a
pull request (docs/adr/0003) before anyone considers it finished — the PR
is a reviewable diff, satisfying `CLAUDE.md`'s working-agreement items
11-12 ("show reviewable diffs," "do not silently rewrite user knowledge")
whether or not a human also paused before the write happened.

## Decision

Treat "human review" as satisfied by either of two checkpoints, chosen by
tempo rather than by mechanism:

- **Deliberate** (today's default CLI feel, and the default for any direct
  tool call with no coordinating skill): a human reads each `*_prepare`
  preview and only then triggers `*_apply`.
- **Fast-capture**: an agent, following the `mcp-coordinator` skill
  (`skills/mcp-coordinator/SKILL.md`), chains `*_prepare` -> `*_apply`
  immediately for the routine case, and only surfaces a preview to the
  human when something in it is genuinely ambiguous — a plausible
  entity-duplicate, a low-confidence or peripheral relevance flag, or any
  `validate_proposal` issue (which is a hard stop regardless of tempo, not
  a judgment call). The PR is the review moment for everything else.

Critically, **the policy for when to pause lives entirely in the skill, not
in wakil's Python code**. `mcp/tools.py`'s `*_prepare`/`*_apply` split
(docs/adr/0018) is what makes this possible: wakil's code always produces a
preview before any write and always requires an explicit second call to
write, for both tempos identically. What differs is only whether the agent
between the two calls chooses to show the preview to a human first. This
means the fast-capture tempo is not a new escape hatch in wakil's write
path — nothing in `mcp/tools.py` or `git_service.py` behaves differently
because a coordinating skill is involved.

The `mcp-coordinator` skill is deliberately not one of the DAG-internal
skills under `src/wakil/skills/` (those are prose folded into wakil's own
model calls, per ADR 0008, and never read by an outer agent). It lives at
the repo-root `skills/mcp-coordinator/SKILL.md` and is also served as an MCP
resource (`wakil://skill/mcp-coordinator`) from the running server, so a
connected client sees it with no manual install step.

## Consequences

- `PROMPT.md`'s "Target Use Case" section and its human-review framing gain
  the busy-operator persona explicitly, so future features aren't judged
  only against "one careful user reading every diff."
- `CLAUDE.md`'s Working Agreement item 11 gains a clarifying note: a PR
  satisfies "show reviewable diffs" for skill-coordinated MCP flows; a
  pre-write console preview satisfies it for direct CLI/tool use. A future
  contributor reading item 11 in isolation should not conclude every write
  must block on a human pre-write, in every context, always.
- Nothing about `validate_proposal`'s hard-stop behavior changes — a
  schema-invalid proposal blocks `*_apply` regardless of which tempo is in
  play. Fast-capture only changes when a human is *shown* a passing
  proposal, never whether an invalid one can be applied.
- If the coordinator skill's pause heuristics turn out to be wrong in
  practice (too eager, or too cautious), that's a prose change to
  `skills/mcp-coordinator/SKILL.md`, not a wakil code change or a new ADR —
  the policy was deliberately kept outside the code for exactly this
  reason.

## Sources

- `docs/adr/0018-mcp-interface.md` (the prepare/apply mechanism this
  decision relies on).
- `docs/adr/0008-ingestion-decomposition-reject-multi-agent-mechanism.md`
  ("a step that must run, runs because code calls it, not because an agent
  remembered to" — the phrase this ADR reinterprets).
- `docs/adr/0003-git-native-change-tracking.md` (the PR as the reviewable-
  diff mechanism).
- `CLAUDE.md`, Working Agreement items 11-12.
- `skills/mcp-coordinator/SKILL.md` (the pause/no-pause policy itself).
