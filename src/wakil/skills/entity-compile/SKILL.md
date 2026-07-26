---
name: entity-compile
description: Re-synthesize an entity's Compiled Truth as the union of every fact already present in its own Timeline — no new source involved. Use for `wakil entities compile SLUG` only, never as a substitute for note-revision's ordinary merge.
skill_api: 1
---

# entity-compile

`note-revision/SKILL.md` already defines the State/Timeline discipline this
skill runs on: Compiled Truth is *re-synthesized* on every update to cover
the union of what was already there plus what's new, and Timeline is
*append-only*, never touched by a synthesis step. This skill doesn't restate
that discipline — read it there. What's different here is narrower: there is
no new source. "What's new" is empty. The entire input is one entity's own
existing Timeline, and the job is to produce a Compiled Truth that actually
reflects it, for notes where synthesis never ran (docs/adr/0016's audit
finding: Compiled Truth empty or placeholder for most of the vault, with the
real content sitting unsynthesized in Timeline).

**Why a separate skill file, not `note-revision` directly:** ADR 0016 asks
for reuse to be tried first, and only split out "if reuse proves genuinely
awkward in practice." It is, here, concretely: `note-revision`'s procedure
(Step 2 "re-read the raw source for the new facts," Step 3 "classify the new
material... additive, contradicting, or duplicate," the three-tier dedup
heuristic, and "Resolving conflicting sources" across multiple sources) is
built entirely around comparing new material to what's already on the page.
Compile has no new material to classify — every fact is already on the page,
by definition. Pointing a model at that procedure with nothing to classify
would be confusing, not merely redundant, so the exception case ADR 0016
itself names applies. What does transfer directly, and is the actual
foundation this skill stands on, is the State/Timeline discipline above —
that part is cited, not restated.

## What this does

Read the given Timeline in full. Write a Compiled Truth paragraph (or a few)
that is the union of every fact it contains — not a diff, not a rewrite from
memory of the entity, a synthesis grounded only in the Timeline text given.

## Hard constraint: additive only

This is not a compression or summarization task. Every fact already present
in the Timeline must be present, in some form, in the Compiled Truth you
write. Worst case, the result is verbose or a little redundant — that's a
safe failure. Dropping a still-true fact is not; it's the same clobbering
failure note-revision's three-tier dedup heuristic and clobbering-bug
warning both exist to prevent, applied here to a case where there's no new
source to classify a fact against — only the question of whether two
Timeline entries are the same fact restated or two distinct ones.

**When in doubt, don't silently resolve it: include both.** If two entries
look like they might describe the same underlying fact (same subject,
close-but-not-identical dates or figures) but you're not certain, keep both
in the output rather than merging them into one or guessing which is stale.
A reader can tolerate two similar sentences; they can't recover a fact that
silently vanished.

## What this does NOT do

- **No lossy "collapse stale detail" compression.** Deciding what's safe to
  drop as outdated is a different, harder judgment with no calibration data
  behind it yet — explicitly out of scope for this skill version (ADR 0016).
  If an old Timeline entry is superseded by a newer one, say so in the
  synthesis (e.g. "originally X, later revised to Y") rather than silently
  omitting the earlier value.
- **Never proposes a Timeline entry.** Timeline is passed through to the
  caller untouched — not edited, reordered, or appended to. That's a
  property of how this skill is invoked (the caller never asks for a
  Timeline edit here), worth stating plainly anyway so a reader doesn't
  assume this is a general-purpose revision pass.
- **Not a substitute for note-revision.** A source with genuinely new
  information about an entity still goes through note-revision's ordinary
  merge; this skill only runs when the "new" material is nothing at all —
  just the entity's own accumulated history, unsynthesized.

## Worked example

`companies/mosaic-private-markets.md` is exactly the shape this skill exists
for: a large Timeline of dated interview and meeting notes, an empty or
placeholder Compiled Truth. Its Timeline might contain entries like:

```text
### 2026-05-02 — intro call
- Mosaic is raising a $40M Series B, targeting a Q3 close.
- Lead investor not yet confirmed.

### 2026-05-20 — follow-up with CFO
- Series B target confirmed at $40M; CFO says they're now in
  term-sheet discussions with two lead candidates.
- Use of proceeds: 60% platform engineering, 40% go-to-market.

### 2026-06-14 — partner meeting
- One of the two lead candidates dropped out over valuation.
- Remaining lead (unnamed) has verbally agreed to term sheet;
  signing expected early Q3.
```

These are related but not redundant: the raise size is confirmed across all
three, the lead-investor status changes entry to entry, and the
use-of-proceeds detail appears only once. A correct Compiled Truth keeps all
of it, distinctly:

> Mosaic is raising a $40M Series B (target confirmed as of 2026-05-20),
> planned for a Q3 close, allocating proceeds roughly 60% to platform
> engineering and 40% go-to-market. Two lead-investor candidates were in
> term-sheet discussions as of 2026-05-20; one dropped out over valuation by
> 2026-06-14, leaving one remaining candidate who has verbally agreed to
> terms with signing expected early Q3.

Note what this is *not*: it doesn't drop the use-of-proceeds split just
because it appeared in only one entry, and it doesn't silently pick "one
lead investor" as the final state without naming that the field narrowed
from two candidates to one over time — both entries' facts survive, in
synthesized form, even though the raw Timeline entries themselves are left
exactly as they were.

## Full resynthesis mode

Everything above is this skill's default, additive-only judgment, used for
the ordinary `wakil entities compile SLUG` call. `wakil entities compile
SLUG --full` (docs/adr/0017, Stage 2) invokes this same skill for a second,
deliberately different judgment: **full resynthesis**, the one place this
skill is allowed to drop content. This section states exactly where that
permission ends; "Hard constraint: additive only" above still governs every
other invocation, including this one's own treatment of Timeline itself —
full resynthesis never touches, reorders, or proposes an edit to Timeline
any more than additive mode does. It also never sees the note's *current*
Compiled Truth — that omission is a property of the prompt you're given
(docs/adr/0017), not a judgment call to make: every full resynthesis is a
fresh re-derivation from Timeline alone, never an edit of a prior run's
output.

**Your output is the synthesized Compiled Truth prose, and nothing else.**
Do not explain, inside your output, what you added, dropped, or why — no
"What was dropped, and why" section, no aside naming a superseded value
you removed, no meta-commentary about the compression itself. Whatever you
return becomes the note's actual Compiled Truth section verbatim; any
explanation of your reasoning bloats exactly the thing this operation
exists to keep small, and a dropped value named in an "originally X, now
Y" aside is still present in the output, which the redundancy rule above
already forbids once a fact is judged clearly superseded. The worked
example below labels its "what was dropped and why" commentary
separately, after the example output, precisely because that commentary
is for a human reading this skill file — it documents the *reasoning*
behind the example, it is not part of the *result* the example shows.

### The two-part salience rule

Two independent judgments decide what full resynthesis may compress or
drop. Apply both; neither alone is the right rule — a frequency-only
reading ("keep only what's repeated" or, just as wrong the other way,
"protect anything stated only once") would license removing almost
nothing, since most of a bloated Compiled Truth is likely distinct,
once-stated content, not redundant restatement.

1. **Redundancy (frequency-based).** The same underlying fact, restated or
   updated across multiple Timeline entries, collapses toward its current
   value. This is the one real behavioral difference from additive mode's
   dedup above: where a later entry clearly and unambiguously supersedes
   an earlier one — not a fuzzy, uncertain match — the earlier restatement
   may be dropped entirely, not merely condensed or kept alongside a
   "originally X, later Y" note. If the two entries aren't clearly the
   same fact narrowing to one value, this rule doesn't apply — fall back
   to "when in doubt, include both," the same as additive mode.
2. **Durability (a fact stated exactly once).** A fact mentioned only once
   isn't redundant with anything, so rule 1 never reaches it. The question
   instead is whether it's *durable* — an identifying detail, a decision,
   a commitment, an ongoing status or relationship fact, the kind of thing
   `note-revision`'s own State discipline treats as belonging in Compiled
   Truth permanently — or *ephemeral* — a scheduling detail, a
   meeting-day operational note whose relevance was spent the day it
   happened. Durable once-stated facts are never eligible for removal,
   full stop, regardless of how far over target the note runs. Ephemeral
   once-stated facts may be compressed or dropped, but only once whatever
   durable consequence they led to is already captured elsewhere in your
   output — a since-superseded "let's meet at 3pm" is droppable once the
   note already reflects that the meeting happened and what came of it,
   not before.

**When genuinely uncertain whether a once-stated fact is durable or
ephemeral, default to durable.** This mirrors the "when in doubt, include
both" bias above, applied to a different ambiguity, because the cost here
is asymmetric too: wrongly calling a fact durable costs a little extra
verbosity in an operation that exists to save space; wrongly calling it
ephemeral silently deletes something true. When size pressure and
uncertainty pull in opposite directions, uncertainty wins.

### Worked example

`people/carla-nunez.md`'s Timeline has grown long enough that its Compiled
Truth is well over target. Relevant entries:

```text
### 2026-03-04 -- first intro
- Carla is the VP of Underwriting at Northfield Mutual.
- She mentioned the team is piloting a new risk-scoring model, no
  timeline given yet.

### 2026-03-18 -- pilot kickoff
- Risk-scoring pilot timeline confirmed: targeting a Q3 go-live, 90-day
  evaluation window against the current model.
- Meeting moved from Tuesday to Thursday this week because Carla was
  traveling -- rescheduled for 2026-03-19 at 2pm.

### 2026-03-19 -- rescheduled check-in
- Confirmed pilot is on track; no material change to the Q3 go-live
  target discussed on 2026-03-18.
- Carla's team lead on the pilot is Priya Shah.
```

Two things a correct full resynthesis does here that additive mode above
would not:

- **Redundancy, resolved by dropping.** The pilot timeline is mentioned
  three times: "no timeline given yet" (2026-03-04), "Q3 go-live, 90-day
  window" (2026-03-18), and "on track, no material change" (2026-03-19).
  These clearly narrow to one current value. Full resynthesis states only
  the current one — a Q3 go-live pilot, on track as of the latest
  check-in — and drops the superseded "no timeline given yet" entirely,
  not condensed alongside it. Additive mode would have kept all three;
  this is the one real behavioral difference between the two modes.
- **Ephemeral fact, compressed away.** The Tuesday-to-Thursday reschedule
  is a once-stated, scheduling-only fact. Its only durable consequence —
  that the 2026-03-19 check-in happened and confirmed the pilot on track —
  is already captured. The reschedule reason and original day are
  dropped; nothing about it was durable in its own right.

What survives intact, and must: Carla's title (VP of Underwriting,
Northfield Mutual) and Priya Shah as the pilot's team lead — both durable,
once-stated identifying/relationship facts, neither eligible for
compression under any size pressure.

**Your entire output would be** — nothing before or after this, no
heading, no explanation:

> Carla Nunez is VP of Underwriting at Northfield Mutual. Her team is
> running a risk-scoring model pilot, on track for a Q3 go-live against a
> 90-day evaluation window as of the 2026-03-19 check-in; Priya Shah leads
> the pilot on Carla's team.

*(For this documentation's benefit, not part of the output above — a
reader of this skill file, not the model producing an output, is the
audience for the rest of this paragraph.)* What it dropped, and why: the
original "no timeline given yet" framing
(superseded by the confirmed Q3 target — redundancy rule) and the
Tuesday-to-Thursday reschedule detail (ephemeral, its consequence already
folded into "as of the 2026-03-19 check-in" — durability rule). Everything
else — the title, the pilot's existence, the Q3 target, the 90-day window,
Priya's role — is durable, or the current value of a redundant chain, and
stays.

### A note on `eval.json`'s repeat-run scenario

The repeat-run scenario in this skill's `eval.json` (eval scenario d) is a
best-effort approximation of ADR 0017's anti-compounding requirement, not
a literal test of it. `tests/evals/runner.py` makes exactly one model call
per scenario, so it can't actually invoke full resynthesis twice in
sequence and compare the two outputs the way the requirement describes.
The scenario instead asks the model to treat a fabricated "prior run's"
Compiled Truth as something to check its own answer against afterward,
not read from — which the model isn't structurally prevented from ignoring
the way the real code path is. **The real guarantee is structural, not
behavioral**: `build_full_resynthesis_prompt` never includes the note's
current Compiled Truth at all, verified directly against actual call
arguments in
`tests/unit/test_ingest_service.py::test_prepare_entity_full_resynthesis_happy_path_and_timeline_only_prompt`.
That unit test, not this eval scenario, is the real evidence for the
no-compounding claim; treat the eval scenario as a supplementary sanity
check on output quality, not the proof.
