---
title: Periodic Entity Compilation and Gated Timeline-Context Trimming
status: proposed
date: 2026-07-25
audience: wakil design
---

# Periodic Entity Compilation and Gated Timeline-Context Trimming

## Context

`wakil enrich`'s entity-revision call (`_run_entity_updates` /
`_revise_candidates`, `src/wakil/app/ingest_service.py`) inlines the
**entire current file content** of every candidate entity note into the
prompt (`build_revision_prompt`, `src/wakil/llm/prompts.py:175-226`). Entity
notes only grow — Timeline is append-only by design
(`note-revision/SKILL.md`) — so this cost is unbounded.
`docs/adr/0015-relevance-gated-entities-and-truncation-driven-batching.md`
already diagnosed a real `ModelTruncatedError` from this (case study:
`companies/mosaic-private-markets.md` ~53KB, `people/edward-bridges.md`
~32KB) and landed two *reactive* mitigations: relevance-gating candidates
before the call, and bisecting a batch by content length after a truncation
occurs. Both work around the size; neither reduces it. ADR 0015's own
Consequences section names the gap directly: "a single entity whose own
existing note is large enough to overflow the budget by itself... is a
real, surfaced failure" — not solved by either of its mechanisms.

Separately, `docs/entity-model.md` (a design doc, not yet implemented)
already proposes a `wakil entity compile <slug>` command: re-synthesize
Compiled Truth from durable memory, diff-preview, commit — but scoped to
one entity and assuming a DB-row-backed Timeline model (ADR 0005) that
isn't actually wired up. Its own audit of the real vault found "Compiled
Truth is empty for most of the vault... well over a hundred lines of raw,
unsynthesized interview notes dumped into Timeline instead" — the
synthesis half of the two-part format has never actually run for most
entities.

This ADR proposes the proactive complement ADR 0015 names as still needed:
keep entity notes themselves smaller in the places that matter for context
cost, rather than only working around their size after the fact.

## Decision

Two mechanisms, shipped together.

**1. `wakil entities compile [SLUG]`** — a new periodic/on-demand command
that re-synthesizes each entity's Compiled Truth from its full Timeline
history, favoring recency: recent entries keep concrete detail; older
entries collapse to the facts that still matter, dropping stale specifics
(a scheduled call that already happened, a "next steps" note superseded by
a later entry). Timeline itself is never touched by this operation —
it stays the complete, intact evidence log. Generalizes
`docs/entity-model.md`'s single-entity design into a workspace-wide sweep,
since "periodic" implies batch:

- No `SLUG`: workspace-wide due-scan (the periodic use case).
- `SLUG` given: compile that one entity now, ignoring the due-check (manual
  override, e.g. right after a big meeting).
- `--type`, `--min-timeline-chars`, `--dry-run`, `--yes`, `--commit` flags,
  mirroring `wakil schema migrate`'s existing plan/diff/confirm/apply shape
  (`schema_migrate_service.py`, `main.py:897-970`) — the closest existing
  precedent for a workspace-wide, non-source-scoped maintenance operation
  with a reviewable diff.

**2. Gated context-trimming**: once a note has been compiled at least once,
`wakil enrich`'s revision call sends only its Compiled Truth (not Timeline)
as context — directly attacking the size that caused the original
truncation. This is conditional, not universal: since most existing entity
notes have empty/placeholder Compiled Truth today, trimming unconditionally
would blind enrichment to most of what's actually known about most
entities until the whole vault has been compiled. The trim applies only to
notes carrying a fresh `compiled_through` marker (see below); everything
else keeps today's full-content behavior as a safe fallback. This couples
the two mechanisms by design: the context-cost benefit is earned per-note
by having run `entities compile` on it, not granted unconditionally. On a
freshly-migrated vault (nothing compiled yet), `wakil enrich` behaves
exactly as it does today — zero regression risk on day one — and cost
shrinks precisely as compilation coverage grows.

### Mechanism 1 (shared prerequisite): factor the note-section split out

`_merge_entity_note` already parses a note into "top section" (between H1
and the Timeline heading) and "Timeline section" via `_H1_RE` /
`_TIMELINE_HEADING_RE` (`ingest_service.py:1016-1085`). Factor this into a
reusable `_split_note_sections(content) -> tuple[Post, h1_line, top, timeline] | None`
helper, used by the merge path, the new compile path, and the gated
context-trim. Pure refactor — `_merge_entity_note`'s own behavior is
unchanged.

### Mechanism 2 detail: `entities compile`

- **New skill** `src/wakil/skills/entity-compile/SKILL.md`, distinct from
  `note-revision` — the judgment is genuinely different. `note-revision`
  merges *new* source material and treats "shorter than before" as a red
  flag; compile has no new source, re-derives Compiled Truth from the
  note's own full Timeline, and shortening old, superseded detail is the
  point, provided every still-true fact remains present somewhere (Compiled
  Truth or the untouched Timeline). Requires an `eval.json` live-model
  scenario before being considered production-ready, per this project's own
  norm for new judgment guidance (ADR 0004) — not just unit tests.
- **New contract** `EntityCompileOutput` (`src/wakil/llm/schemas.py`): one
  `compiled_truth: str` field. One entity per call, deliberately not
  batched like the revision call — there's no shared cacheable prefix
  across unrelated entities' Timelines the way there is across candidates
  sharing one enrich source document, so batching here buys nothing and
  reintroduces the exact truncation risk this ADR exists to reduce. Each
  call's size is bounded by that single note's own content, not compounded
  across entities. The residual risk of one pathologically long Timeline
  overflowing a single call is real (named in ADR 0015) and left as a
  surfaced warning, not solved by new chunking logic here.
- **New prompt builder** `build_compile_prompt(top_section, timeline_section)`
  in `llm/prompts.py` — simpler than `build_revision_prompt`, no source
  document or cacheable-prefix split needed.
- **New frontmatter field** `compiled_through: <date>`, stamped by
  `entities compile`, meaning "Compiled Truth already reflects every
  Timeline entry as of this date." Distinct from `updated:`, which is
  stamped on any content change including a plain Timeline append during
  `wakil enrich` and so can't distinguish "just grew" from "was actively
  re-synthesized." Due-check: never compiled and Timeline exceeds
  `--min-timeline-chars` (default TBD at implementation, e.g. 4000 —
  Timeline character length as a computable proxy for cost, the same
  precedent `_split_candidates_by_content_length` already established); or
  compiled before, but new entries past `compiled_through` push accumulated
  growth over that same threshold.
- **New service module** `src/wakil/app/entity_compile_service.py`
  (mirrors `schema_migrate_service.py`'s shape: `plan_entity_compilation`/
  `apply_entity_compilation`, own `CompileProposal`/`CompilePlan`
  dataclasses) rather than growing the already-1450-line
  `ingest_service.py` further. `apply_entity_compilation` re-reads each
  file immediately before writing and skips (reports, doesn't silently
  overwrite) anything changed since planning — the same stale-file guard
  `apply_migrations`/`apply_enrichment` already use. Reuses
  `_merge_entity_note`'s top-section-replace logic for the actual write,
  with `compiled_through` added to `frontmatter_updates` alongside
  `updated`; Timeline section passed through byte-identical.
- **New CLI group** `entities_app`, `app.add_typer(entities_app,
  name="entities")` — plural, matching the existing `sources` group
  precedent. `--commit` reuses the existing `"chore"` commit kind
  (`git_service.commit_change`), no `COMMIT_PREFIXES` change needed — same
  category as `schema migrate --commit`.

### Mechanism 2 detail: gated trim in the revision call

In `_run_entity_updates`, when building `candidates`, also read each
target's `compiled_through` frontmatter and Timeline size. A new
`_is_compile_fresh(post, timeline_section, min_timeline_chars) -> bool`
gate decides, per candidate, whether `build_revision_prompt` receives that
target's full content (today's behavior, the default) or just its top
section via `_split_note_sections` (once compiled and not yet stale).
`_merge_entity_note`'s merge step is unaffected either way — it always
re-reads the full file at apply time regardless of what the model saw,
since merging needs the real Timeline to prepend into.

### Passive nudge from `wakil enrich`

When `_run_entity_updates` touches a candidate whose Timeline has grown
past `min_timeline_chars` since its last `compiled_through` (or was never
compiled and already exceeds it), add one batched `proposal.warnings`
entry: "N entities have grown large enough to benefit from compilation —
run `wakil entities compile`." Visible, never auto-triggered — the same
advisory pattern already used for relevance exclusions and stale-file
skips, consistent with `CLAUDE.md`'s "avoid hidden background behavior."

## Alternatives considered

- **Trim Timeline from every candidate's context unconditionally,
  immediately.** Rejected: the real vault audit in `docs/entity-model.md`
  found Compiled Truth empty/placeholder for most existing entity notes —
  an unconditional trim would blind enrichment to most of what's actually
  known about most entities until a full-vault compile sweep completes.
  Gating per-note on `compiled_through` avoids any regression window at the
  cost of the benefit phasing in gradually rather than landing all at once.
- **Batch multiple entities per compile call**, mirroring
  `_revise_candidates`'s bisection shape. Rejected: that batching exists
  because many candidates share one cached source-document prefix during
  `wakil enrich`; no equivalent shared prefix exists across unrelated
  entities' Timelines during a compile sweep, so batching adds truncation
  risk without a caching benefit to offset it. One call per entity keeps
  each call's size bounded by a single note.
- **Drive Timeline entries off `Memory` rows (ADR 0005) instead of parsing
  Markdown directly**, to get a structured, queryable due-check for free.
  Rejected for this ADR: ADR 0005's `event_date`-backed model is accepted
  but not implemented — nothing in `apply_enrichment` currently links a
  `Memory` row to the literal Timeline text prepended into a note. Building
  this ADR's due-check against real Markdown (the same source
  `_merge_entity_note` already parses) ships against what actually exists;
  converging with ADR 0005 later is a separate, still-open decision, not a
  blocker here.
- **A `page_versions`/snapshot table to make compilation reversible at the
  DB layer.** Rejected, consistent with ADR 0007 (Markdown is source of
  truth, git provides version history): `entities compile`'s "before" state
  is recoverable via `git log`/`git diff` on the note file itself, the same
  as every other durable-content change in this codebase.

## Consequences

- `wakil enrich`'s revision-call cost shrinks precisely as
  `entities compile` coverage grows across the vault — no benefit on day
  one, growing benefit as the command is run, with zero regression risk in
  between because untouched notes keep today's full-content behavior.
- A genuinely new judgment surface (`entity-compile` skill) is introduced
  and, per ADR 0004's own norm, is not considered production-ready until it
  has a live-model eval scenario — this ADR does not consider that
  optional polish.
- Timeline remains the durable, complete, human-auditable evidence log this
  project's principles require (`CLAUDE.md`: "do not silently rewrite user
  knowledge") — no mechanism here ever edits, reorders, or shortens it.
  Everything "compressed" is specifically the derived Compiled Truth
  summary, always re-synthesizable from the untouched Timeline if a past
  compile is ever judged wrong.
- A new, small vocabulary item (`compiled_through` frontmatter field) is
  added to `compiled-truth-timeline`-shaped entity schemas; whether this
  needs an explicit schema-catalog change or fits under existing permissive
  frontmatter validation needs confirming against `validate_proposal` at
  implementation time.
- Residual truncation risk is not fully eliminated: a single entity whose
  own Timeline is large enough to overflow one compile call by itself is
  left as a surfaced warning, not new chunking logic — the same class of
  gap ADR 0015 already accepted for the revision call, not solved by either
  ADR.
- This does not converge with ADR 0005's `Memory`-backed Timeline-entry
  model; both remain accepted-but-not-unified designs until a later,
  separate decision reconciles them.

## Sources

- `docs/adr/0015-relevance-gated-entities-and-truncation-driven-batching.md`
  (the reactive mitigations this ADR complements; names the "single large
  entity" gap this ADR still doesn't close)
- `docs/entity-model.md` (`wakil entity compile <slug>` design origin; the
  "Compiled Truth is empty for most of the vault" audit finding driving the
  gated-rollout decision)
- `docs/adr/0005-timeline-entries-as-memory-rows.md` (considered and
  deliberately not converged with, see Alternatives)
- `docs/adr/0007-markdown-source-of-truth-sqlite-operational-store.md`
  (why no snapshot table)
- `docs/adr/0004-exclude-live-model-skill-evals-from-default-ci.md` (the
  live-eval norm this ADR's new skill is held to)
- `src/wakil/app/ingest_service.py`, `_run_entity_updates`,
  `_merge_entity_note`, `_split_candidates_by_content_length`
- `src/wakil/app/schema_migrate_service.py` (the plan/apply/stale-guard
  shape this ADR's new service module follows)
- `src/wakil/skills/note-revision/SKILL.md` (State vs. Timeline discipline
  the new `entity-compile` skill deliberately diverges from, and why)
- `CLAUDE.md`, "Working Agreement for Agents" (11: show reviewable diffs;
  12: do not silently rewrite user knowledge) and "Design Biases" (avoid
  hidden background behavior)
