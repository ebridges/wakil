---
title: <Decision-stating title, not a topic label — "X over Y", not "X">
status: proposed
date: YYYY-MM-DD
audience: wakil design
---

<!--
  Derived from the structure of docs/adr/0001-0020. Copy this file to
  docs/adr/<NNNN>-<slug>.md (next sequential number, zero-padded to 4
  digits; slug matches the title) and fill in each section. Delete every
  HTML comment (including this one) once the section above it is written —
  they're instructions, not content.

  House style observed across every existing ADR, not just structure:
  - No H1 heading duplicating the frontmatter title — Context is the first
    visible heading.
  - Every non-obvious claim is grounded in something a reader can go check:
    a file path (with a line number when it pins a specific fact), another
    ADR number, a commit SHA, a PR number, or a session transcript path.
    Prose reasoning with no anchor reads as asserted, not decided.
  - Consequences includes real costs and known gaps, not just benefits —
    see the "drift risk" bullet in 0018 or the "known, accepted gap" bullet
    in 0020 for the register to aim for.
-->

## Context

<!--
  What situation makes a decision necessary right now? Ground it in the
  concrete pressure — an incident, a conflicting requirement between two
  existing docs/ADRs, a gap the code doesn't yet cover — not just "it would
  be nice to have X." If this decision only makes sense in light of another
  ADR or a docs/*.md design doc, name and quote it (see 0018's second
  paragraph for the pattern of naming the specific risk a naive approach
  would reintroduce).
-->

## Decision

<!--
  State what was decided, as a direct claim, then break it into the
  concrete mechanism if it has moving parts (see 0018's bulleted
  sub-decisions: packaging, transport, tool shape, etc.). Say what was
  *not* included as clearly as what was — 0018's "deliberately out of
  scope" bullet is the pattern. This is the section a future reader skims
  to answer "what did we actually agree to."

  If status is `proposed` rather than `accepted`, 0012 uses "Proposed
  decision" here and "Consequences (if accepted and implemented)" below —
  match that phrasing so the tentative status is visible in the headings
  themselves, not just the frontmatter.
-->

<!--
## Alternatives considered

  Optional — include when there were real contenders worth recording, not
  a strawman. One bullet per alternative: what it was, and why it was
  rejected (or deferred, and under what condition it'd be revisited — see
  0016's "worth adding back later... as its own decision with its own
  eval"). Place this section between Decision and Consequences, as in
  0015/0016/0017/0020.
-->

<!--
## Supersession relative to ADR NNNN

  Optional — include only when this ADR explicitly changes or replaces a
  decision made in an earlier one (see 0017 relative to 0016). Say so
  explicitly rather than leaving a reader to infer it from a stale reading
  of the older ADR: which specific bullet/decision is superseded, and
  whether it's a full reversal or a refinement (0017 distinguishes
  "superseded — picked up, not left deferred" from "superseded — refined,
  not replaced wholesale"). Cross-link both directions if practical.
-->

## Consequences

<!--
  What follows from the decision — for the codebase, for future work, for
  what becomes harder or easier. Include the real costs and known gaps
  alongside the benefits (drift risk between duplicated logic, a
  deliberately accepted limitation, a question left open for a future
  decision), not just an upside list. A Consequences section with no
  downside is usually missing something.
-->

## Sources

<!--
  Every non-obvious claim above should trace to something here: a file
  path (`src/wakil/foo.py:123`), a docs/*.md section by name, another ADR
  by number and filename, a commit SHA, a PR number, or a session
  transcript path with a line number and short quote (see 0007's and
  0004's Sources sections for the citation format). This section is what
  makes the ADR verifiable rather than asserted — don't skip it because
  the reasoning "seems obvious" in Context/Decision.
-->
