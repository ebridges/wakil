---
title: <What was decided, stated as a decision rather than a topic label>
status: template
date: YYYY-MM-DD
audience: wakil design
---

# 0000-template

<!--
  Derived from the structure of docs/adr/0001-0020. Copy this file to
  docs/adr/<NNNN>-<slug>.md (next sequential number, zero-padded to 4
  digits; slug is a short kebab-case abbreviation of the title, not a
  literal derivation of it — cf. 0018-mcp-interface.md for "MCP server
  interface, exposed as prepare/apply tool pairs") and fill in each
  section. Two things need changing that aren't sections: replace the H1
  above with your own file's slug (see the H1 rule below), and delete every
  HTML comment (including this one) once the section above it is written —
  they're instructions, not content.

  Frontmatter:
  - Set `status:` to `proposed` or `accepted` (this file carries `template`
    only so the unfilled skeleton doesn't read as a live proposal when the
    ADR set is skimmed by title and status).
  - `title:` should state the decision, not name the topic — "Markdown as
    source of truth, SQLite as operational store" (0007), not "Storage."
    A comparative "X over Y" form (0002, "QMD as First-Class Search Over
    SQLite FTS5") works when the decision really was a choice between two
    named options, but most titles aren't comparative and shouldn't be
    forced into it.
  - Sentence case for the title, which is where recent ADRs (0018-0020)
    have landed; Title Case was used in the past, but sentence case is
    preferred going forward.

  House style, beyond structure:
  - Open with an H1 that is the file's own kebab-case slug — i.e. the
    filename without its `.md` extension, matching the `# 0000-template`
    above. For an ADR saved as `0021-drop-the-widget-cache.md` that is
    `# 0021-drop-the-widget-cache`. `## Context` is the next heading.

    This is a new standard set here, not a description of the corpus: no
    existing ADR uses it. Ten open with an H1 carrying the prose title
    (0002, 0004-0006, 0013-0017, 0020) and ten open with no H1 at all
    (0001, 0003, 0007-0012, 0018, 0019). Those are deliberately not being
    backfilled — the rule applies to ADRs written from here on. A
    filename-shaped H1 is unambiguous, cannot drift from the filename, and
    gives markdownlint's MD041 the top-level heading it wants without
    restating the frontmatter title (which is why `.markdownlint.yaml` sets
    `MD025: front_matter_title: ""` — it stops the frontmatter `title:`
    from counting as a second H1).
  - Every non-obvious claim is grounded in something a reader can go check:
    a file path (with a line number when it pins a specific fact), another
    ADR number, a commit SHA, a PR number, or a session transcript path.
    Prose reasoning with no anchor reads as asserted, not decided.
  - Consequences includes real costs and known gaps, not just benefits —
    see the "drift risk" bullet in 0018 (line 86) or the "known, accepted
    gap" sentence in 0015 (line 332) for the register to aim for.
  - In past docs (e.g. 0015-0017) there is content between the H1 header
    and the `## Context` section. Sometimes, it may be helpful to put a
    brief abstract or TLDR passage in this location (for example if it's
    a long/verbose ADR).
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
  scope" sentence is the pattern. This is the section a future reader skims
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

<!--
## Implementation

  Optional but strongly preferred — record what actually landed, and when.
  Used by 0008 (line 33), 0009, 0010, 0011, and 0012 (line 296), placed
  between Consequences and Sources. One bullet per PR: number, title, and
  the dates it was opened and merged (0008's bullets are the format to
  copy). For a `proposed` ADR, say so plainly instead of omitting the
  section — 0012 records "Not yet started. This ADR is the proposal..."
  along with the PR that carried the document itself.

  This section is what keeps the gotcha in docs/DEVELOPMENT.md ("An ADR's
  `status: accepted` means the decision was made, not that it was
  implemented") from biting the next reader: a decision whose consequences
  never landed in code looks identical to a shipped one from the
  frontmatter alone.
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
