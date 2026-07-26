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
