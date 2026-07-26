---
title: Periodic Entity Compilation and Gated Timeline-Context Trimming
status: accepted
date: 2026-07-25
audience: wakil design
---

# Periodic Entity Compilation and Gated Timeline-Context Trimming

`status: accepted` covers a substantially narrower decision than this ADR's
original draft proposed. It went through adversarial review (see Sources)
that surfaced a verified, concrete defect in the original Mechanism 2
("gated context-trimming") design and a scope/risk case for shrinking
Mechanism 1 before building it. What's accepted: a single-entity, additive-
only compile pilot (Mechanism 1, narrowed). What's explicitly **rejected as
specified**, not deferred-with-hope: gated context-trimming in the
`wakil enrich` revision call (original Mechanism 2) and lossy "collapse
stale detail" compression. See Decision for exactly what that means and
why.

## Context

`wakil enrich`'s entity-revision call (`_run_entity_updates` /
`_revise_candidates`, `src/wakil/app/ingest_service.py`) inlines the
**entire current file content** of every candidate entity note into the
prompt (`build_revision_prompt`, `src/wakil/llm/prompts.py:175-226`). Entity
notes only grow — Timeline is append-only by design
(`note-revision/SKILL.md`) — so this cost is unbounded.
`docs/adr/0015-relevance-gated-entities-and-truncation-driven-batching.md`
diagnosed a real `ModelTruncatedError` from this (case study:
`companies/mosaic-private-markets.md` ~53KB, `people/edward-bridges.md`
~32KB) and landed two *reactive* mitigations: relevance-gating candidates
before the call, and bisecting a batch by content length after a truncation
occurs, capped at a bounded recursion depth. **As of this ADR's adversarial
review, that fix is implemented, tested, and live-validated against the
real originally-failing transcript** — the acute, observed truncation
failure is resolved. ADR 0015's own Consequences section still names one
narrower, currently theoretical gap neither of its mechanisms closes: "a
single entity whose own existing note is large enough to overflow the
budget by itself." That gap, not the (now-fixed) batch-truncation failure,
is the truncation-side motivation for this ADR — worth naming precisely,
since conflating it with the already-solved failure overstates how urgent
the truncation case for this ADR actually is.

The independently real, and stronger, motivation is separate: `docs/
entity-model.md` (a design doc, not yet implemented) already proposes a
`wakil entity compile <slug>` command, motivated by its own audit of the
real vault finding "Compiled Truth is empty for most of the vault... well
over a hundred lines of raw, unsynthesized interview notes dumped into
Timeline instead" — the synthesis half of the note format's own two-part
design has never actually run for most entities. This is a knowledge-
quality problem, true regardless of whether truncation is also a live
concern, and it's what this ADR is actually justified by.

This ADR proposes a narrow pilot toward that second, stronger
justification — not the full proactive complement to ADR 0015 an earlier
draft proposed. See Decision.

## Decision

### Accepted: a single-entity, additive-only compile pilot

**`wakil entities compile SLUG`** (slug required, no workspace-wide sweep
yet) re-synthesizes one entity's Compiled Truth as the union of every fact
already present across its full Timeline history. **Additive only**: this
pilot does not implement "recent entries keep detail, older entries
collapse" lossy compression — the only validated problem (`docs/
entity-model.md`'s audit: Compiled Truth is empty/placeholder for most of
the vault) is fixed by synthesizing it at all, not by compressing it.
Additive synthesis has a safe failure mode by construction: worst case it's
verbose or redundant; it cannot silently drop a still-true fact the way a
"what's safe to compress" judgment can. Timeline itself is never touched —
it stays the complete, intact evidence log regardless.

- **No new skill file yet.** `note-revision/SKILL.md` already specifies
  exactly this judgment for the ordinary State-merge case: re-synthesize
  Compiled Truth as "the union of what was already there plus what's new."
  For compile, "what's new" is empty — the source *is* the note's own
  Timeline. Try driving the compile prompt off `note-revision`'s existing
  discipline before writing `entity-compile/SKILL.md` as a distinct
  judgment surface; only split it out if reuse proves genuinely awkward in
  practice (e.g. `note-revision`'s other Timeline-entry-specific guidance
  confuses the model when there's no new entry to append). This also means
  `EntityRevisionOutput`/`build_revision_prompt` may be reusable rather
  than needing a wholly new `EntityCompileOutput`/`build_compile_prompt` —
  confirm once real prompts are drafted, don't assume either way yet.
- **One call per entity**, no batching — the compile prompt has no shared
  cacheable prefix across unrelated entities' Timelines the way candidates
  sharing one `wakil enrich` source document do, so batching would add
  truncation risk without a caching benefit to offset it (unchanged from
  the original draft's reasoning here). The residual risk of one
  pathologically long Timeline overflowing its own compile call is real —
  notably, the notes most "due" for compilation (Mosaic ~53KB,
  edward-bridges ~32KB) are also the most likely to hit this — and is left
  as a surfaced warning, not solved by new chunking logic in this pilot.
- **`--yes`/`--commit` only, no `--dry-run` sweep flags yet** — those, and
  a due-check (`--min-timeline-chars`, `compiled_through` frontmatter
  field, workspace-wide scan), are explicitly deferred (see below), not
  part of what's accepted here.
- **Hard eval gate, not a soft IOU**: this pilot does not touch a real note
  outside of a live-model eval scenario (ADR 0004) run and reviewed first.
  ADR 0015's `relevance` field shipped with "required before considered
  complete" language and ran live before that eval existed — the resulting
  judgment instability (the same source's Brian Corr rated `central` in
  one run, `minor` in another) was only caught by accident, re-running the
  same source twice. Compile's failure mode is worse if wrong — Compiled
  Truth is what `wakil query`'s `_build_contexts` actually shows the user
  (the first ~2000 chars of the file, i.e. the H1 + Compiled Truth region
  for a compiled note) — so this pilot does not repeat that pattern.

### Rejected as specified: gated context-trimming in `wakil enrich`

An earlier draft of this ADR proposed a second mechanism: once a note
carries a `compiled_through` marker, `wakil enrich`'s revision call would
send only its Compiled Truth, not full content, cutting the context-cost
that (theoretically, for a single oversized note — see Context) risks
truncation. This is rejected as specified, not deferred-with-hope: its
safety argument — "`_merge_entity_note`'s merge step is unaffected either
way — it always re-reads the full file at apply time" — is factually wrong
about the code that now exists. `_run_entity_updates` reads each
candidate's content exactly once into `content`; that same in-memory
string flows through `_revise_candidates` (the model prompt),
`_apply_entity_revisions` (`old_content = by_path.get(...)`, the merge
input), and `apply_enrichment`'s stale-file guard (`update.old_content`,
compared against a fresh disk read) — there is no re-read at apply time.
Trimming that one shared string for the prompt would, for every compiled
candidate: silently mark up-to-date entities as "changed since prepared"
in the stale-guard (a trimmed string can never equal a fresh full read),
break `_merge_entity_note` (no `## Timeline / Log` heading left to find,
`_TIMELINE_HEADING_RE` fails to match, misreported as a shape violation),
and corrupt `_split_candidates_by_content_length`'s cost-balancing (it uses
`len(candidate[2])` as its proxy — exactly the field this mechanism would
selectively shrink). Fixing this requires carrying two separate content
values per candidate (trimmed-for-prompt, full-for-merge/stale-guard/
bisection-sizing) through `_EntityCandidate` and every function that
touches it — a real design change, not a refactor, and not something to
retrofit onto ADR 0015's just-landed, live-validated machinery without its
own dedicated design pass. Revisit as a separate, later ADR once the
compile pilot above has proven its judgment is trustworthy — there is no
value in shrinking `wakil enrich`'s context around a compile step whose
own output isn't yet trusted.

### Explicitly deferred, not part of this decision

- Lossy "collapse stale detail" compression (only additive synthesis is
  accepted now; revisit once additive compile is running and trusted).
- The workspace-wide due-scan / `--min-timeline-chars` / `compiled_through`
  frontmatter field / `--dry-run` sweep and the `entities_app` CLI group —
  all deferred until the single-entity pilot's judgment (and, if it turns
  out to be needed, a dedicated skill) is proven on real notes.
- Gated context-trimming in `wakil enrich` (rejected above, not deferred —
  but a redesigned version addressing the content-duality problem could be
  a legitimate future ADR).
- `--min-timeline-chars` as a truncation-risk predictor: if a due-check is
  built later, frame Timeline length as a *note-readability* proxy for
  humans, not a truncation-risk predictor — ADR 0015 already established
  that content length is a poor predictor of the (thinking-token-dominated)
  truncation cost this ADR's Context section discusses, for the same
  reasons that applied to its own batching decision.

## Alternatives considered

- **Lossy "collapse stale detail" compression from day one**, the original
  draft's framing. Rejected for this pilot: the only validated problem is
  that Compiled Truth is empty, which additive-only synthesis already
  fixes with a safe failure mode (verbose, never wrong). Deciding what's
  "stale" has zero calibration data behind it and is exactly the harder,
  unvalidated judgment a sibling field (ADR 0015's `relevance`) already
  showed can be unstable run-to-run on identical input. Worth adding back
  later, once additive compile is running and trusted, as its own decision
  with its own eval — not bundled into the first cut.
- **A new `entity-compile/SKILL.md` immediately**, the original draft's
  framing. Deferred, not rejected: `note-revision/SKILL.md` already
  specifies "re-synthesize as the union of what was already there plus
  what's new," which is structurally the same judgment compile needs (with
  "new" being empty). Try that before assuming a distinct skill file is
  warranted — writing one prematurely is exactly the kind of speculative
  abstraction `CLAUDE.md` argues against if the existing discipline turns
  out to cover it.
- **Trim Timeline from every candidate's context unconditionally,
  immediately** (gated context-trimming's would-be simpler alternative).
  Moot for this pilot — gated context-trimming itself is rejected as
  specified (see Decision), so there's no trim to make unconditional or
  gated in the first place. Recorded for whoever picks up a redesigned
  version later: an unconditional trim would still blind enrichment to
  most entities' real history until a full-vault compile sweep completes,
  for the same reason the original gating design cited.
- **Batch multiple entities per compile call**, mirroring
  `_revise_candidates`'s bisection shape. Rejected: that batching exists
  because many candidates share one cached source-document prefix during
  `wakil enrich`; no equivalent shared prefix exists across unrelated
  entities' Timelines during a compile pass, so batching adds truncation
  risk without a caching benefit to offset it. One call per entity keeps
  each call's size bounded by a single note.
- **Drive Timeline entries off `Memory` rows (ADR 0005) instead of parsing
  Markdown directly**, to get a structured, queryable due-check for free.
  Moot for this pilot (no due-check is being built yet), and would face the
  same objection when it is: ADR 0005's `event_date`-backed model is
  accepted but not implemented — nothing in `apply_enrichment` currently
  links a `Memory` row to the literal Timeline text prepended into a note.
  A future due-check should build against real Markdown (the same source
  `_merge_entity_note` already parses), matching what actually exists.
- **A `page_versions`/snapshot table to make compilation reversible at the
  DB layer.** Rejected, consistent with ADR 0007 (Markdown is source of
  truth, git provides version history): `entities compile`'s "before" state
  is recoverable via `git log`/`git diff` on the note file itself, the same
  as every other durable-content change in this codebase.

## Consequences

- No `wakil enrich` cost reduction from this pilot — that was gated
  context-trimming's benefit, and it's rejected as specified. This pilot's
  payoff is knowledge quality (Compiled Truth actually reflecting an
  entity's history), not revision-call cost.
- The compile pilot does not touch a real note until it has a live-model
  eval scenario reviewed (ADR 0004) — a hard prerequisite for this ADR,
  not the softer "required before considered complete" framing ADR 0015's
  `relevance` field used, which shipped and ran live before its own eval
  existed.
- Timeline remains the durable, complete, human-auditable evidence log this
  project's principles require (`CLAUDE.md`: "do not silently rewrite user
  knowledge") — this pilot never edits, reorders, or shortens it. Compiled
  Truth is always re-synthesizable from the untouched Timeline if a past
  compile is ever judged wrong — true even more directly for additive-only
  synthesis, which by construction never has content to "recover."
- No new frontmatter field (`compiled_through`), CLI group, or due-check
  ships with this pilot — all deferred pending real judgment calibration,
  not implicitly promised for a follow-up PR.
- Residual truncation risk is not addressed: a single entity whose own
  Timeline is large enough to overflow one compile call by itself is left
  as a surfaced warning, not new chunking logic — and, notably, this is
  likely to be exactly the notes most worth compiling (Mosaic,
  edward-bridges), not an edge case unrelated to the pilot's actual use.
- This does not converge with ADR 0005's `Memory`-backed Timeline-entry
  model; both remain accepted-but-not-unified designs until a later,
  separate decision reconciles them.
- Gated context-trimming (rejected above) leaves ADR 0015's own named gap
  — a single oversized note overflowing the revision call — genuinely
  unaddressed. That gap remains open, theoretical (not yet observed in
  production), and explicitly out of scope for this ADR.

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
  precedent originally cited for a workspace-wide sweep service; noted as
  a superficial shape match during review — mechanical linter vs. lossy
  LLM summarizer — see Decision's "deferred" scope)
- `src/wakil/skills/note-revision/SKILL.md` (State/Compiled-Truth synthesis
  discipline this ADR's pilot tries to reuse directly, rather than
  assuming a distinct `entity-compile` skill is needed)
- `src/wakil/app/query_service.py` (`_build_contexts`, `NOTE_EXCERPT_CHARS`)
  — Compiled Truth is what a compiled note's first ~2000 characters give
  `wakil query`, which is why this ADR treats the eval gate as hard, not
  optional polish
- `CLAUDE.md`, "Working Agreement for Agents" (11: show reviewable diffs;
  12: do not silently rewrite user knowledge) and "Design Biases" (avoid
  hidden background behavior, avoid speculative abstraction)
- Adversarial review, two rounds (2026-07-25): surfaced the verified defect
  in the original gated context-trimming design (`_apply_entity_revisions`/
  `apply_enrichment`'s stale-guard reuse the same in-memory content read
  once at prepare-time, never re-read at apply time — trimming it for the
  prompt would silently corrupt the stale-guard, the merge, and
  `_split_candidates_by_content_length`'s cost balancing all at once), and
  the case for narrowing Mechanism 1 to additive-only synthesis before any
  lossy compression judgment is trusted
