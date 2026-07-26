---
title: Bounded Compiled Truth — Target-Size Warning and Gated Full Resynthesis
status: accepted
date: 2026-07-26
audience: wakil design
---

# Bounded Compiled Truth — Target-Size Warning and Gated Full Resynthesis

This ADR is Stages 1 and 2 of a staged migration proposed at the end of
`docs/adr/0016-entity-compilation-and-gated-timeline-context-trimming.md`'s
review process, prompted directly by a user question about that ADR's own
additive-only design: if compile never discards anything, how is repeated
compilation different from just restating Timeline, and what happens to
Compiled Truth over a long enough history? That question is legitimate and
this ADR's Context section answers it before proposing anything.

Written and reviewed while PR #33 (ADR 0016's `wakil entities compile SLUG`
pilot) is still open, not yet merged to `main` — deliberately, per the
sequencing this ADR's own review was asked to follow (ADR first, merge
second, Stage 1/2 implementation third). References to `prepare_entity_
compile`, `apply_entity_compile`, and `entity-compile/SKILL.md` throughout
this document describe that pending code as it exists on the
`feat/entity-compile-pilot` branch; if it changes materially during merge
review before landing, re-check those references before implementing
Stage 1/2 against them.

## Context

### The gap ADR 0016 accepted, on purpose, but didn't yet solve

ADR 0016 shipped `wakil entities compile SLUG` (PR #33): single-entity,
additive-only re-synthesis of Compiled Truth from an entity's own Timeline.
"Additive-only" means informationally complete — no still-true fact is ever
dropped — not "textually unprocessed": the skill already deduplicates
restated facts and reorganizes chronological Timeline entries into a
coherent, topic-organized narrative (see `entity-compile/SKILL.md`'s worked
example, and the live-eval scenario
`redundant-entries-compile-without-duplication`, which specifically tests
that overlapping entries don't just get restated three times). That's real
synthesis, not concatenation.

But it has a structural ceiling: additive-only synthesis can shrink
*redundancy* but can never shrink *content*. Over a long enough Timeline, an
entity's Compiled Truth will keep growing — deduplicated, but unbounded —
because the mechanism has no way to judge that some fact, still technically
true, is no longer worth keeping in the "current state" summary. ADR 0016
knew this and named it explicitly in its own Alternatives Considered
section rather than treating it as solved: lossy compression was "worth
adding back later, once additive compile is running and trusted, as its own
decision with its own eval — not bundled into the first cut." This ADR is
that later decision, arriving sooner than "later" implied only because the
question was asked directly, not because anything broke.

### Why this matters concretely, not just in principle

`wakil query`'s `_build_contexts` (`src/wakil/app/query_service.py:17,98,119`)
reads a note's first `NOTE_EXCERPT_CHARS = 2000` characters as its context
for grounding an answer — frontmatter YAML, the H1 line, and Compiled Truth
all compete for that same 2000-character window before Timeline is even
reached. A Compiled Truth that has grown large doesn't just read as
verbose to a human — past a certain point it pushes real, still-true
content outside the window `wakil query` actually sees, silently
undermining the exact thing Compiled Truth exists to be: the note's
authoritative, quickly-readable current state. This gives a concrete,
already-codified-elsewhere anchor for what "too big" means, rather than an
arbitrary number invented for this ADR.

### External research: how this problem is handled elsewhere

Four sources were reviewed specifically for how other long-running-context
systems manage exactly this tension (a full raw history that must be kept,
alongside a bounded working summary that must stay usable). Summary of
each, and how it did or didn't shape this ADR:

1. **["Multi-Layered Approach for Context Summarization in Long-Running AI
   Agents"](https://medium.com/@kevaljagani1/multi-layered-approach-for-context-summarization-in-long-running-ai-agents-2a7826fc3a5f)**
   (Medium, Keval Jagani). Proposes a cascade: cheap, deterministic
   reduction first (truncating verbose tool output, a sliding window over
   old messages), LLM summarization only as a last resort — "Summarization
   is expensive, slow, and inherently lossy. It should be the last resort,
   not the default." Large content is stored as an external artifact with
   only a lightweight reference kept in the working context.
   **Influence:** confirms our existing architecture is already the right
   shape — Timeline (full, on-disk, referenced) plus Compiled Truth (small,
   in-context-friendly) *is* the artifact/reference split this source
   describes — and confirms the layering principle already implicit in
   ADR 0015 → ADR 0016 → this ADR's own sequencing (cheap deterministic
   filtering, then a safe LLM operation, lossy LLM compression last). Not a
   design change; a validation of sequencing already in place.

2. **["Incremental Summarization"](https://agenticskillset.org/en/topics/incremental-summarization/)**
   (Agentic Skillset). A rolling summary lives as its own structured
   artifact, updated by a merge step that "preserves all decisions,
   constraints, and current work state," and "overwrites superseded
   information... with a note it was revised" rather than silently
   replacing it — close to identical to what `entity-compile/SKILL.md`
   already does. Two things this source has that we didn't: an explicit
   **target token budget** to stop unbounded growth, and a **periodic full
   rewrite** (its example: every fifth cycle) as a separate operation from
   the routine incremental merge, specifically to correct drift that pure
   incremental patching accumulates over many cycles.
   **Influence:** directly adopted. The target-size trigger (Decision,
   Stage 1) and the two-mode split — routine additive merge vs. a
   separate, rarer full-resynthesis operation (Decision, Stage 2) — are
   this source's two central ideas, applied to Compiled Truth/Timeline.

3. **["In-Context Autoregressive Summarization: A Chain-of-Key
   Approach"](https://arxiv.org/html/2407.15021v1)** (arXiv 2407.15021).
   Its core update rule is stated almost identically to our own additive
   constraint: **"Updates should never reduce the amount of information."**
   That's real, independent validation that incremental, non-lossy updates
   are a sound, published, evaluated technique, not merely a cautious
   choice specific to this project. Under a genuinely constrained token
   budget, though, it does apply lossy compression, using three criteria:
   redundancy elimination (already covered by additive-only's
   deduplication), relevance filtering, and **frequency emphasis** —
   facts recurring across multiple source entries are scored as more
   important.
   **Influence:** partially adopted, with a correction made twice over —
   once in the first draft of this ADR, and once more after adversarial
   review caught that the first correction wasn't sharp enough. Frequency-
   of-mention is adopted as one salience signal for Stage 2 — it's testable
   and calibratable in a way "is this still relevant" alone isn't — but
   taken as the *only* signal, "keep only what's frequently mentioned" is
   the wrong rule for a personal knowledge base, and "protect anything
   stated only once" (this ADR's own first-draft fix) turns out to be too
   blunt in the other direction: it would also protect a one-time, already-
   moot logistics note ("meeting moved to 3pm") exactly as strongly as a
   one-time birthday, which means it would fail to shrink most of what's
   actually likely to be low-value bloat. Stage 2's design below (see
   "Salience rule") uses frequency for redundancy only, and adds a second,
   orthogonal dimension — durability vs. ephemerality — to decide what a
   once-stated fact is actually worth protecting for. The paper's
   structured-JSON-representation technique (which independently
   outperforms plain text under tight budgets) was considered and not
   adopted — see Alternatives Considered.

4. **["Effective context engineering for AI agents"](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)**
   (Anthropic engineering blog). Compaction is triggered by approaching a
   capacity limit, not by elapsed time. Rollout methodology: "start by
   maximizing recall to ensure your compaction prompt captures every
   relevant piece of information..., then iterate to improve precision by
   eliminating superfluous content" — exactly the sequencing ADR 0016 → this
   ADR already follow (prove additive/lossless first, add lossy compression
   only once trusted). Structured external note-taking (a persistent file
   outside the model's working context) is its recommended pattern for
   durable agent state — which is simply wakil's whole premise, Markdown
   notes as external memory. Notably, Anthropic gives **no prescriptive
   checklist** for what's safe to discard, recommending empirical tuning
   instead — the same reason ADR 0016 deferred compression rather than
   guessing at rules, and the same reason this ADR treats Stage 2 as
   needing its own eval gate rather than shipping on confidence alone.
   **Influence:** validates the size-triggered-not-time-triggered choice
   (Decision, Stage 1) and the staged-trust rollout methodology already in
   use across ADR 0015/0016/this ADR. No new mechanism adopted from it
   directly; it's the closest match to the process this project was already
   running.

## Decision

### Stage 1: a target size for Compiled Truth, surfaced as an interactive choice, not silently enforced

- New constant (name TBD at implementation, e.g. `_COMPILED_TRUTH_TARGET_CHARS`),
  anchored to `NOTE_EXCERPT_CHARS` (2000) with headroom subtracted for
  typical frontmatter + H1 overhead — a starting default around 1200-1500
  chars, tunable via real usage and eval, the same "concrete anchor, exact
  value tuned at implementation" precedent ADR 0016 set for
  `--min-timeline-chars`. That range is calibrated against the two known
  large, under-synthesized notes (`companies/mosaic-private-markets.md`,
  `people/edward-bridges.md`) that motivated this whole line of work, not
  against "the vault" broadly — most ordinary entity notes have
  meaningfully smaller frontmatter and Compiled Truth, and are expected to
  never trigger this mechanism at all. That's the intended behavior, not a
  gap: the target only needs to be right for the notes that actually get
  large.
- **Stateless check, no new frontmatter.** The trigger is simply "is the
  compiled_truth this compile run just produced longer than target" —
  evaluated fresh each time, not tracked against a persisted baseline. This
  was a real simplification made while writing this ADR: an earlier draft
  of this decision considered a `compiled_through`/`last_compiled`-style
  frontmatter marker to track growth-since-last-resynthesis, but nothing in
  Stage 1 or Stage 2 as scoped actually needs it — a stateless "is it over
  target right now" check already satisfies "size trigger, not time
  trigger," and adding persisted state for a distinction that doesn't
  change any behavior would be exactly the kind of unnecessary complexity
  `CLAUDE.md` warns against. If a note oscillates near the threshold and
  the warning fires repeatedly across separate compiles, that's an accepted
  minor nuisance (see Consequences), not solved with more state here.
- **Where it fires, and what it offers — per the user's explicit
  requirement that this be an interactive choice, not a passive log line.**
  In `wakil entities compile SLUG`'s existing preview step (before the
  existing apply-confirmation prompt): if the compile's resulting
  `compiled_truth` exceeds target, present the size and three choices,
  matching this project's established preview-before-write discipline
  rather than adding a new one:
  1. **Apply as-is** — proceed to the existing confirm/apply flow
     unchanged.
  2. **Edit directly** — open the proposed `compiled_truth` text in the
     user's `$EDITOR` via `click.edit()` (the standard, already-tested way
     a CLI offers this, not a bespoke subprocess integration), then:
     - If `click.edit()` returns `None` (the file's mtime never changed —
       Click's own signal for "nothing was saved"), treat this as a
       cancelled edit and return to this same three-choice menu.
     - If the returned text is empty or whitespace-only, that is *not*
       silently treated as "no change" the way `_merge_entity_note`
       already (correctly, for its own reason) treats an omitted
       `compiled_truth` from the model — that fallback exists to protect
       against a model that forgot to resend content, and reusing it here
       would silently discard a user's deliberate edit and reapply the
       original over-target text with no error shown. Instead: reject the
       empty result with a clear message ("Compiled Truth can't be
       emptied via edit — write real content, or cancel") and return to
       this same three-choice menu, the same recovery target as every
       other reject-and-retry case here — **not** an automatic
       re-invocation of `click.edit()`. An earlier version of this
       decision distinguished "return to the menu" from "return straight
       to editing" for this case; implementing that distinction literally
       produces a genuine, demonstrated infinite loop, because
       `click.edit()` can return the exact same invalid content on every
       call (a misconfigured `$EDITOR`, or a user re-saving the same
       mistake) and an automatic retry has no bound and no way out short
       of eventually producing valid text. Caught by writing the actual
       code and running the test suite, not by review of the design alone
       — see `docs/TROUBLESHOOTING.md`. One extra keystroke to reselect
       "Edit" is a small cost for removing a real hang risk.
     - If the returned text itself contains a line matching
       `_TIMELINE_HEADING_RE` (e.g. the user pastes or writes their own
       "## Timeline" subheading), reject it before it's ever written — a
       future `_split_note_sections` call on this note would match that
       line instead of the real Timeline heading further down, silently
       corrupting the top/Timeline boundary for every subsequent merge or
       compile. Report the conflict and return to this same three-choice
       menu, for the same reason as the empty-result case above.
     - Otherwise, re-run the same size check against the edited text. If
       it's still over target, loop back to this three-choice menu rather
       than silently proceeding — the menu fires until the size check
       clears or the user picks apply-as-is/full-resynthesis/cancel, not
       once.
  3. **Run full resynthesis instead** — abandon this additive result and
     invoke Stage 2 (below) on the same entity, continuing into *its*
     preview/confirm flow.
  Cancel remains available at any point, matching every other wakil write
  path's "Aborted; nothing was written."
- **Considered and deferred, not included: a matching passive check in
  `wakil enrich`.** An earlier draft of this decision proposed reviving
  what an *early, pre-narrowing* draft of ADR 0016 had sketched as a
  "passive nudge" — but that language does not appear anywhere in ADR
  0016's actual, accepted text (verified: `grep -i nudge` against it
  returns nothing); it was silently dropped during that ADR's narrowing,
  folded into the general workspace-sweep deferral without ever being
  evaluated on its own merits. Citing it as a "revival" of reviewed prior
  art was inaccurate, caught in this ADR's own adversarial review, and
  removed rather than patched. On its own merits, adding a third routinely-
  firing warning type to `proposal.warnings` (after ADR 0015's relevance
  exclusions, which that ADR already flagged as trending toward ignorable
  noise) needs its own real justification for why it belongs on that
  specific channel rather than, say, a separate summary line, or nowhere
  yet — that's a genuine open question this ADR is not resolving today.
  Left for a later decision if it's still wanted; not part of this scope.
- **No workspace-wide sweep.** The trigger above only fires where a note is
  already being touched by `entities compile` itself — no new bulk scan,
  due-check, or CLI flags, and (per the previous bullet) no `wakil enrich`
  integration either. The workspace-wide sweep ADR 0016 deferred remains
  deferred; see Supersession.

### Stage 2: full resynthesis — the only place lossy compression is allowed

- New mode on the existing command, e.g. `wakil entities compile SLUG
  --full` (exact flag name TBD at implementation) — distinct from, not a
  replacement for, today's default additive mode. Additive stays the
  default and the only mode Stage 1's "apply as-is"/"edit" choices ever
  invoke.
- **What "full" means, and why its prompt is deliberately narrower than
  additive mode's, not "the same."** An earlier draft of this decision said
  full resynthesis "re-synthesizes... the same way additive mode does,"
  reusing the same prompt shape — which sends the note's *current* top
  section (Compiled Truth) to the model as context alongside Timeline, the
  same way `build_compile_prompt` does for additive mode today. Caught in
  review: that's fine for additive mode, where the current top section is
  just one more thing that can only ever be added to, never lost. For a
  mode that's allowed to drop content, showing the model "here is the
  current summary" alongside the full Timeline risks anchoring it on
  whatever a *previous* run already decided to compress, rather than
  re-deriving fresh from source every time — and if that bias is real, it
  compounds across repeated invocations with no floor. Fixed by
  specification: **the full-resynthesis prompt is Timeline-only.** It does
  not include the note's current Compiled Truth at all. Every full
  resynthesis is a genuinely fresh re-derivation from the one thing that's
  actually immutable, not an edit of the last one.
- **Salience rule, corrected twice — see Context, source 3, for the first
  correction and why it wasn't sharp enough on its own.** Two independent,
  orthogonal judgments, not one:
  1. **Redundancy** (frequency-based): the same underlying fact restated or
     updated across multiple Timeline entries collapses toward its current
     value. Where an earlier value is clearly and unambiguously superseded
     by a later, more specific restatement, full resynthesis is allowed to
     drop the earlier value entirely (a brief "originally X, revised to Y"
     note is a style choice, not a requirement) — additive mode must always
     keep both.
  2. **Durability** (a fact stated exactly once): a once-stated fact is not
     redundant with anything, so rule 1 never applies to it — the question
     is whether it's *durable* (an identifying detail, a decision, a
     commitment, an ongoing status or relationship fact — the kind of
     thing `note-revision`'s own State discipline treats as belonging in
     Compiled Truth permanently) or *ephemeral* (a scheduling detail, a
     meeting-day operational note whose relevance was spent the day it
     happened). Durable once-stated facts are never eligible for removal,
     full stop, regardless of size pressure. Ephemeral once-stated facts
     may be compressed or dropped once the resynthesis has already captured
     whatever durable consequence they led to (e.g. a since-superseded
     "let's meet at 3pm" is droppable once the note already reflects that
     the meeting happened and what came of it). The cost of this judgment
     is asymmetric — wrongly calling something durable just costs a little
     verbosity, wrongly calling it ephemeral silently deletes a true fact —
     so on genuine uncertainty the default must be durable, the same "when
     in doubt, include both" bias additive mode's own skill already applies
     to redundancy calls; the eventual Stage-2 skill addendum should state
     this explicitly, not leave it implicit.
  This two-part rule is what actually gives Stage 2 real compression power
  beyond what additive mode's own deduplication already provides — a
  frequency-only rule (this ADR's first-draft version) would have licensed
  removing almost nothing, since most of a bloated Compiled Truth is likely
  distinct, once-stated content, not redundant restatement.
- **What happens if a note is still over target after full resynthesis.**
  Not every note will fit — an entity with a long, genuinely durable
  history may legitimately never compress under target, and that's a valid
  outcome, not a failure. State this plainly rather than leaving it
  ambiguous: report the before/after size, and if still over target, say so
  explicitly (e.g. "reduced from X to Y chars, still over the Z-char
  target — this entity has substantial durable history; no further
  automated action is offered") rather than implying success or failure
  either way.
- **Hard eval gate, matching ADR 0016's now-established discipline exactly
  — no exceptions for this being a "smaller" change than the original
  pilot.** A dedicated `eval.json` (distinct from the existing
  `entity-compile` scenarios, or extending that same file with clearly
  Stage-2-specific scenarios) must include, at minimum:
  (a) a fact repeated across several entries compiles to one clear
      statement, with the superseded earlier value actually gone from the
      output, not optionally retained — the previous draft of this eval
      requirement accepted "either dropped or condensed" as equally
      passing, which meant a Stage 2 that behaved identically to additive
      mode (i.e. did nothing new) could still pass; tightened here to
      require the behavioral delta Stage 2 actually exists to provide;
  (b) a fact stated exactly once, judged durable (an identifying detail or
      a decision), survives full resynthesis intact;
  (c) a fact stated exactly once, judged ephemeral and already moot (a
      superseded scheduling detail), is compressed or dropped — the direct
      test that Stage 2 has real power, not just protection, and the
      scenario the frequency-only version of this rule could never have
      passed;
  (d) running full resynthesis twice in immediate succession on the same
      note does not lose more content on the second run than the first —
      the direct test for the anchoring/compounding risk the Timeline-only
      prompt design above is meant to prevent by construction.
  Every scenario must be run live and pass (`uv run pytest -m eval -k
  <id>`, per ADR 0004) before Stage 2 touches any real note — the same
  standard ADR 0016 set and met for the additive pilot, not relaxed here.
- **No new frontmatter marker required for eligibility.** An earlier
  version of this plan (informal, pre-ADR) proposed gating full
  resynthesis on the entity having already been additively compiled at
  least once. On reflection while writing this decision, that gate doesn't
  reduce Stage 2's actual risk — the compression judgment's risk is
  intrinsic to the mechanism itself, not lowered by whether this specific
  note happened to go through additive mode first — and the real,
  meaningful gate is the eval requirement above, which applies to the
  mechanism once, not per-note. Dropped rather than carried forward. This
  does *not* mean repeated Stage 2 invocations are unguarded — see the
  Timeline-only prompt design and eval scenario (d) above, which address
  that risk directly rather than through a frontmatter gate.

## Alternatives considered

- **Structured (JSON-like) intermediate representation for Compiled
  Truth**, per Chain-of-Key's demonstrated token-efficiency gains under
  tight budgets. Not adopted: it cuts against `CLAUDE.md`'s Markdown-as-
  source-of-truth bias for a benefit that matters most at token budgets far
  tighter than one note's Compiled Truth section, and would require a
  parse/render layer between the stored Markdown and whatever consumes the
  structured form. Worth revisiting only if a much larger context-budget
  problem materializes elsewhere in the codebase.
- **Time-based (periodic/scheduled) trigger for full resynthesis**,
  mirroring a cron-style "recompile everything monthly." Rejected in favor
  of the size trigger: Anthropic's own compaction guidance and ADR 0015's
  established skepticism of arbitrary schedules both point the same
  direction — trigger on the actual constraint being protected (context
  size), not on elapsed time, which has no necessary relationship to
  whether a note has actually grown.
- **Fully automatic compression whenever a note is over target**, with no
  user choice. Rejected outright, per the user's explicit requirement that
  the warning present real options rather than silently act, and
  consistent with this project's standing principle (`CLAUDE.md`: "do not
  silently rewrite user knowledge") that has governed every write path in
  this codebase so far.
- **Blanket "keep only frequently-mentioned facts" compression**, the
  literal reading of Chain-of-Key's frequency-emphasis criterion. Rejected
  explicitly — see the corrected salience rule in Decision, Stage 2 — as
  wrong for a personal KB where uniquely-stated facts are common and
  important.
- **A `compiled_through`/`last_compiled` frontmatter marker** for either
  Stage 1's trigger or Stage 2's eligibility gate. Rejected for Stage 1 as
  unneeded state for a check that's already stateless (see Decision, Stage
  1). Rejected for Stage 2 because the eval-gate gets the actual safety
  benefit that a per-note "was it compiled before" marker was meant to
  provide, without the schema/frontmatter-catalog questions ADR 0016 itself
  flagged as unresolved for a similarly-named field.
- **A passive size warning wired into `wakil enrich`, alongside the
  interactive one in `entities compile`.** Considered and left out of this
  ADR's scope, not silently dropped — see Decision, Stage 1. The candidate
  citation for this as "already-considered territory" turned out to be
  inaccurate (ADR 0016's accepted text never contained it), and on its own
  merits it would add a third routinely-firing warning type to
  `proposal.warnings`, a channel ADR 0015 already flagged as trending
  toward background noise for its first such warning. Revisit as its own
  decision if it's still wanted, with that signal-to-noise question
  actually engaged rather than assumed away.

## Supersession relative to ADR 0016

Explicit, so a reader trusts this ADR over a stale reading of 0016:

- **Superseded — picked up, not left deferred:** ADR 0016's "Explicitly
  deferred" bullet "Lossy 'collapse stale detail' compression (only
  additive synthesis is accepted now; revisit once additive compile is
  running and trusted)" is superseded by this ADR's Stage 2 — which is the
  exact revisit that bullet, and ADR 0016's Alternatives Considered section
  even more explicitly ("worth adding back later... as its own decision
  with its own eval"), already anticipated. This is continuity, not a
  reversal — ADR 0016 named the condition ("once additive compile is
  running and trusted") under which this would happen, and PR #33 running
  with a passing hard eval gate is that condition being met.
- **Superseded — refined, not replaced wholesale:** ADR 0016's deferred
  "workspace-wide due-scan / `--min-timeline-chars`" is partially
  superseded: the *signal* is now specified concretely (a size comparison
  against a `NOTE_EXCERPT_CHARS`-anchored target, checked within `entities
  compile`'s own flow) rather than a vaguely-sketched, unimplemented CLI
  flag. The *bulk-scan CLI machinery itself* (a due-check sweep with
  `--dry-run`, the `entities_app` group gaining new flags for it, and any
  `wakil enrich`-side integration — considered for this ADR and explicitly
  left out, see Decision, Stage 1) remains deferred, now further specified
  as "Stage 3" if it's still wanted once Stage 1/2 have
  run in practice — see Consequences.
- **Not superseded — still in force, unchanged:** gated context-trimming
  in `wakil enrich`'s revision call (ADR 0016's rejected Mechanism 2)
  remains rejected as specified; this ADR does not reopen it, does not
  touch `_run_entity_updates`'s candidate-content plumbing, and does not
  reduce `wakil enrich`'s revision-call cost. The one-call-per-entity, no-
  batching constraint for any compile-family call carries forward
  unchanged (Stage 2 shares the reasoning ADR 0016 gave for Stage 1). The
  hard-eval-gate-before-real-use discipline ADR 0016 established carries
  forward and is applied to Stage 2 without relaxation.

## Consequences

- Compiled Truth gets a real, working ceiling for the first time — not
  enforced silently, but visible and actionable within `entities compile`'s
  own flow, the one point this ADR wires it into (see Decision, Stage 1, on
  why a second, `wakil enrich`-side point was considered and left out).
- The interactive edit/re-check loop in `entities compile` (apply / edit,
  looped until clear / full resynthesis / cancel) is meaningfully more new
  surface area than a single one-shot prompt — it needs to handle a
  cancelled edit, an emptied edit, and an edit that itself corrupts the
  note's section structure, all caught in this ADR's own review rather than
  left as unstated edge cases. It should reuse `click.edit()` rather than a
  hand-rolled editor integration, keeping the added complexity proportionate
  to what it now explicitly covers.
- A note sitting just above/below the target threshold across repeated
  touches may see the Stage 1 warning fire on more than one occasion for
  the same underlying growth — an accepted, minor nuisance in exchange for
  not building persisted growth-tracking state that nothing else in this
  ADR's scope needs (see Alternatives Considered).
- Stage 2 is real, tested-in-principle lossy compression — the first place
  this project has ever knowingly allowed a model to omit content from a
  knowledge-base page. Unlike additive mode, this is not a safe-by-
  construction failure mode: the durability judgment can be wrong. The
  two-part salience rule (redundancy vs. durability, not frequency alone),
  the Timeline-only prompt design (no compounding across repeat runs), and
  the four-scenario hard eval gate are all load-bearing, not polish — Stage
  2 does not ship, and does not touch a real note, without all three.
- The workspace-wide sweep (a "Stage 3," if ever built) is better-
  specified by this ADR than it was left by ADR 0016 — a concrete size
  trigger to check against, rather than a guessed character threshold —
  but is not part of this decision and is not implicitly promised.
- `wakil enrich`'s revision-call cost is unaffected — this ADR, like ADR
  0016 before it, is entirely about Compiled Truth's own size and quality,
  not about what `_run_entity_updates` sends to the model.

## Sources

- `docs/adr/0016-entity-compilation-and-gated-timeline-context-trimming.md`
  (the pilot this ADR extends; see Supersession for exactly what carries
  forward, what's picked up, and what stays out of scope)
- `docs/adr/0015-relevance-gated-entities-and-truncation-driven-batching.md`
  (established precedent for size-based, evidence-gated triggers over
  arbitrary thresholds)
- `docs/adr/0004-exclude-live-model-skill-evals-from-default-ci.md` (the
  live-eval norm Stage 2's hard gate applies)
- `src/wakil/app/query_service.py` (`NOTE_EXCERPT_CHARS = 2000`,
  `_build_contexts`) — the concrete anchor for what "too big" means
- `src/wakil/app/ingest_service.py` (`_run_entity_updates`,
  `prepare_entity_compile`, `apply_entity_compile`, `_split_note_sections`)
- `src/wakil/skills/entity-compile/SKILL.md` and `eval.json` (PR #33 — the
  additive-only mechanism and its existing worked example/scenarios this
  ADR builds on)
- `CLAUDE.md`, "Working Agreement for Agents" (12: do not silently rewrite
  user knowledge) and "Design Biases" (avoid speculative abstraction)
- External research, read and summarized in full in Context above:
  - Keval Jagani, ["Multi-Layered Approach for Context Summarization in
    Long-Running AI Agents"](https://medium.com/@kevaljagani1/multi-layered-approach-for-context-summarization-in-long-running-ai-agents-2a7826fc3a5f)
  - Agentic Skillset, ["Incremental Summarization"](https://agenticskillset.org/en/topics/incremental-summarization/)
  - ["In-Context Autoregressive Summarization: A Chain-of-Key Approach"](https://arxiv.org/html/2407.15021v1),
    arXiv:2407.15021
  - Anthropic, ["Effective context engineering for AI agents"](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
