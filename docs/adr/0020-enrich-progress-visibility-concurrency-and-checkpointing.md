---
title: Progress visibility, concurrency, and phase checkpointing for wakil enrich
status: accepted
date: 2026-08-03
audience: wakil design
---

# Progress visibility, concurrency, and phase checkpointing for wakil enrich

## Context

`wakil enrich <source-id>` runs `prepare_enrichment`'s fixed 4-call DAG
(ADR 0008: extraction, entity resolution, entity revision, stub-content
synthesis) behind a single static `console.status(...)` spinner
(`cli/main.py`), and gates the whole run on `validate_proposal`'s
intentional all-or-nothing check (ADR 0019's "reviewable diff" guarantee
depends on this: nothing is written until the entire proposal validates)
before anything is written to disk or the database.

On a large, multi-entity source this run can take 20-30 minutes with zero
incremental feedback, and — because nothing is written until
`validate_proposal` passes — a crash, a killed process, or a validation
failure discards every completed model call's output. A retry redoes the
entire DAG from scratch, even when the failure was unrelated to any of the
4 model calls' actual content.

This was traced this session from a concrete incident: a 21-entity email
source (`kb-professional` workspace, source #11, 2026-08-02) ran ~29.5
minutes under the static spinner, then failed `validate_proposal` because
8 of 12 update candidates hit an identical schema-enum gap
(`relationship: 'contact'` not in the allowed set) — a gap unrelated to any
of the 4 model calls' output, since fixed upstream by commit `1be685e`
(PR #169). Nothing was written; the entire 29.5 minutes of extraction,
resolution, revision, and synthesis work had to be redone on retry, purely
because of a downstream schema-validation rule. See
`docs/TROUBLESHOOTING.md` (2026-08-02 entry) for the full incident detail.

Three independent, incrementally-shippable improvements were scoped this
session to address this, explicitly ruling out two larger, more invasive
alternatives:

- **Per-entity/chunked batching** of the revision and synthesis calls was
  rejected — it would partially reverse ADR 0015's batching rationale
  (batch first, split only on measured truncation; see ADR 0015's
  Alternatives Considered for why a precomputed chunk size was already
  rejected there for the identical reason: truncation cost isn't reliably
  predictable from request content ahead of time).
- **Partial-apply on `validate_proposal` failure** (writing whatever
  passed and reporting the rest as skipped) was rejected — it would
  reverse ADR 0019's atomicity guarantee that a reviewable proposal is
  either written in full or not at all. Neither ADR is reversed by this
  decision; both stay intact.

## Decision

Land three independently valuable, independently revertible changes to
`prepare_enrichment`'s existing DAG, in this order:

### 1. Incremental progress feedback (commit `dbb165e`)

An optional `on_progress: Callable[[str], None] | None = None` parameter,
threaded keyword-only through `prepare_enrichment` ->
`_populate_proposal_from_models` -> `_run_entity_resolution` and called at
each of the 4 phase boundaries with a short human-readable string. Default
`None` is a no-op, so every existing caller (tests, any future MCP path)
is unaffected. `_run_entity_updates`/`_synthesize_stub_content` only
announce themselves when they have real candidates/stubs to process —
mirroring their own existing `if not candidates: return` /
`if not stub_entities: return` early-exit guards, so a no-op phase never
emits a message. `cli/main.py`'s `_prepare_enrichment_or_exit` wires this
to the already-open `console.status(...)` spinner via
`status.update(status=msg)`.

### 2. Concurrent entity revision and stub synthesis (commit `74061da`)

`_run_entity_updates` (DAG node 3, entity revision) and
`_synthesize_stub_content` (DAG node 4, stub-content synthesis) touch
disjoint entity sets — update targets vs. create-stub paths — but ran
strictly sequentially. They now run concurrently via a 2-worker
`concurrent.futures.ThreadPoolExecutor` inside `_run_entity_resolution`.

The real ordering dependency was never node 3 before node 4 directly — it
was the three suppression passes sitting between them
(`_suppress_stubs_matching_updates` and friends), which prune
`proposal.stub_entities` using node 3's own output before node 4 used to
run. Node 4 now takes an explicit pre-suppression snapshot
(`stub_snapshot = list(proposal.stub_entities)`) instead of reading
`proposal.stub_entities` live, so it can run alongside node 3 without
racing on that list; the snapshot's `ProposedFile` objects are the same
instances still referenced by `proposal.stub_entities`, so a `.content`
mutation still lands correctly for whatever survives suppression, and any
synthesis work for a stub suppression later discards is simply thrown
away with it (bounded cost — all such targets already share one batched
call, never a separate round trip).

`ThreadPoolExecutor`, not `asyncio`: `llm/client.py` is fully synchronous
end to end, and introducing an async runtime for two blocking,
GIL-releasing I/O calls with no shared mutable state beyond append-only
lists (`proposal.warnings`, `proposal.entity_updates`) would be a
disproportionate change for what it buys. This is plain code-level
concurrency between two calls the DAG already makes deterministically —
it does not reintroduce the agent-decided control flow ADR 0008 rejected;
the DAG's topology (which calls happen, and when) is still entirely
code-determined, only their scheduling on the CPU is concurrent.

Two accepted, explicitly-flagged behavior changes: `proposal.warnings`
insertion order across the two calls is no longer deterministic (cosmetic
only), and a stub that suppression later discards can now have a wasted
synthesis call spent on it.

### 3. Per-phase checkpointing and resume (commit `8e4db80`)

A new `EnrichmentCheckpoint` table (migration 0007) persists each of the
4 phases' output as it completes. Each of the 4 call sites in
`_populate_proposal_from_models`/`_run_entity_resolution`/
`_run_entity_updates`/`_synthesize_stub_content` checks for a matching,
non-stale checkpoint before making a model call, and saves one after a
clean completion.

**Staleness key**: `sha256(source.content_hash | context_digest | model)`,
computed once in `prepare_enrichment` and threaded down as `checkpoint_hash`.
A mismatch — source content, supplied context, or model changed since the
checkpoint was written — means the phase is redone from scratch, never
partially reused across a changed input. This mirrors
`_resume_source_branch`'s (`git_service.py`) "if the assumption that made
resuming valid doesn't hold, start fresh" shape.

**Only clean completions are checkpointed, never a failure.** Revision and
synthesis never raise — they degrade to a warning plus a placeholder/
unchanged content on any model failure — so their checkpoint always
reflects the actual final, accepted state of that phase either way (this
matches ADR 0015's own bisection philosophy: a batch that still fails at
the depth ceiling is accepted as final, not retried further, even in a
live, non-checkpointed run). Extraction and resolution's model calls *do*
have a failure branch that skips the checkpoint save entirely and returns/
raises instead, so a transient failure (a flaky provider call, a passing
network blip) stays retriable on the very next invocation rather than
becoming permanently sticky until `--force` — the staleness key alone
can't distinguish "this will fail identically forever" from "this failed
once," and caching the former case indistinguishably from the latter
would trade an occasional wasted retry for an occasionally permanent
false failure, which is the wrong tradeoff.

`_build_stub_entities` (the pure-code step between resolution's raw output
and `proposal.stub_entities`) is deliberately never itself checkpointed —
only `entity_resolutions` is. It re-runs fresh from whichever resolutions
end up on the proposal, live or resumed alike, so it can never drift from
what a live run would produce for the same input.

`--force` clears every checkpoint for the source up front, in
`prepare_enrichment`, before phase 1 — a forced re-analysis never reuses
stale phase output. A successful `apply_enrichment` clears them too (the
resume window is closed once the source is actually enriched). A declined
preview or a failed `validate_proposal` deliberately leaves them in
place — that is the entire point of this feature, not an oversight.

`_save_checkpoint` opens its own short-lived session per call rather than
sharing one across Part 2's two threads — safe under the existing WAL +
30-second `busy_timeout` (`storage/database.py`), which already exists
specifically for this kind of near-simultaneous short write from
independent processes/threads.

## Alternatives considered

- **Per-entity/chunked batching of revision and synthesis calls**, to cap
  any single call's worst-case duration. Rejected — see Context above;
  this would partially reverse ADR 0015's own considered rejection of a
  precomputed chunk size for the identical reason (truncation cost isn't
  reliably predictable ahead of time from request content).
- **Partial-apply on `validate_proposal` failure** (write whatever passed,
  report the rest as skipped), which would have addressed the source #11
  incident more directly at apply time. Rejected — reverses ADR 0019's
  atomicity guarantee. Checkpointing (Decision 3) addresses the same
  incident from the other direction: instead of writing a partial result,
  it makes the *redo* after fixing the unrelated cause (here, the schema
  gap) near-instant rather than partial-but-real.
- **A checkpoint per bisection leaf** (i.e., persisting each sub-batch of
  `_revise_candidates`'s recursive splitting individually), which would
  shrink the unit of work lost to a crash mid-bisection even further.
  Rejected for this round as unnecessary complexity: bisection only
  triggers on a truncating response for an already-relevance-filtered
  batch (ADR 0015), which in practice is a small minority of runs: the
  common case is one call per phase, and the whole-phase checkpoint
  already turns a crash mid-DAG into "redo at most one phase," which is
  the load-bearing improvement. This is a known, accepted gap (see
  Consequences), not an oversight.

## Consequences

- A large multi-entity `wakil enrich` run's spinner text now changes
  across all 4 phases instead of staying frozen; phase counts shown
  (e.g. stub count) are pre-suppression and may be slightly imprecise —
  accepted rather than adding a second pass just to get an exact number.
- Wall-clock for the revision + synthesis portion of a run drops roughly
  in proportion to how balanced those two calls' durations are, for any
  source with both update candidates and surviving create-stubs.
  `proposal.warnings` ordering across those two calls is no longer
  deterministic (cosmetic).
- A killed/crashed run, or one that fails `validate_proposal` for a reason
  unrelated to the 4 model calls' output (the source #11 shape exactly),
  now resumes from the last completed phase on the next invocation instead
  of redoing every model call — turning what was a full redo into a
  near-instant one for that specific, and apparently not rare, failure
  mode.
- Checkpointing's own accepted risks: a transient extraction/resolution
  failure that happens to recur identically on retry (e.g. a genuinely
  broken prompt/schema mismatch, not a flaky network blip) will be retried
  every time rather than becoming a fast, cached failure — a deliberate
  choice (see Decision 3) to avoid the worse failure mode of a permanently
  sticky false negative. Bisection-leaf granularity is not checkpointed
  (see Alternatives Considered) — a crash mid-bisection redoes the whole
  revision phase, not just the incomplete sub-batches. A workspace file
  changed between a checkpoint's save and its later application is not a
  new risk this introduces: `apply_enrichment`'s existing stale-file guard
  (re-read-and-compare before write) already covers a resumed
  `EntityUpdate.old_content` going stale the same way it covers a
  same-run one.
- `EnrichmentCheckpoint` rows are a purely operational cache, invisible to
  normal `wakil` usage — inspectable directly via
  `sqlite3 <workspace>/wakil.db 'select * from enrichment_checkpoints'`
  for debugging, but never surfaced in any CLI output.

## Sources

- `docs/TROUBLESHOOTING.md`, 2026-08-02 entry (the motivating incident:
  source #11, `kb-professional` workspace, the schema-enum failure fixed
  by commit `1be685e`/PR #169)
- `docs/adr/0008-ingestion-decomposition-reject-multi-agent-mechanism.md`
  (the fixed, code-sequenced DAG this ADR adds visibility/concurrency/
  checkpointing around, without changing its topology)
- `docs/adr/0015-relevance-gated-entities-and-truncation-driven-batching.md`
  (batching/bisection rationale — why per-entity/chunked batching and a
  bisection-leaf checkpoint were both rejected here)
- `docs/adr/0019-fast-capture-review-tempo.md` (the atomicity/reviewable-
  diff guarantee partial-apply would have reversed)
- `src/wakil/app/ingest_service.py`: `prepare_enrichment`,
  `_populate_proposal_from_models`, `_run_entity_resolution`,
  `_run_entity_updates`, `_synthesize_stub_content`,
  `_checkpoint_content_hash`, `_load_checkpoint`/`_save_checkpoint`/
  `_clear_checkpoints`
- `src/wakil/cli/main.py`, `_prepare_enrichment_or_exit`
- `src/wakil/storage/schema.py`, `EnrichmentCheckpoint`;
  `src/wakil/storage/migrations/versions/0007_enrichment_checkpoint.py`
- `src/wakil/storage/database.py` (WAL + `busy_timeout` concurrency
  precedent this relies on for concurrent checkpoint writes)
- `src/wakil/app/git_service.py`, `_resume_source_branch` (the
  "start fresh if the resume assumption doesn't hold" precedent the
  staleness key mirrors)
- Commits: `dbb165e` (progress feedback), `74061da` (concurrency),
  `8e4db80` (checkpointing) — all on branch
  `enrich-progress-concurrency-checkpointing`
