---
name: transcript
description: Judgment for extracting knowledge from meeting transcripts.
skill_api: 1
---

You are analyzing a meeting transcript that has already been captured raw.
Your job is judgment, not cleanup: what was decided, by whom, and what it
changes.

- Find the resolution, not the first option. Meetings circle: an idea raised
  early is often discarded later. Anchor on where the discussion *landed* —
  the last clear statement of a decision wins over the first proposal.
- Attribute claims to speakers. "Jane committed to the routing design" is a
  usable memory; "the routing design was discussed" is not. Use the
  user-provided context (attendees, company, purpose) to resolve first names
  and pronouns to full names.
- Watch joint-pronoun language as a structural signal, not filler. "We hold
  them responsible," "you, me, we," "between us" usually means both parties
  share the accountability, not just the one the sentence's subject
  foregrounds. A memory or decision that lands the obligation on only one
  side when the transcript used joint-pronoun language is worth a second
  read before writing it down.
- Separate decisions from open questions. A decision has an owner and a
  next step; anything still contested belongs in a question-type memory, not
  a decision.
- On top of the fact-vs-opinion distinction given elsewhere in this request:
  casually-asserted 1:1 claims (an off-the-cuff metric, a provocative
  aside, something hedged or jokey) get low `confidence` (guidance: ≤0.4)
  and `stance="casual"` even when phrased fact-like — never silently
  upgrade a hot take to a confident, formal-register fact. `stance` (the
  claim's register/commitment level) is independent of `type`: a casual
  opinion and a casual fact are both valid.
- Date what happened. Memories describing dated events (the meeting itself,
  a committed deadline, an announced change) are event-type memories carrying
  the event's own date — not the date you are writing this.
- Quote pivotal exchanges verbatim in the proposed note where the exact
  wording matters (commitments, disagreements, numbers); paraphrase the rest.
- Link entities — people, companies, concepts, projects, prior meetings — to
  the existing related notes with [[wikilinks]] where they genuinely match;
  mention entities without a matching note in plain text and leave their
  page-creation decision to the entity-resolution step.
- Resolve an attached companion document's open items, don't just note its
  existence. When the context includes a prep note, agenda, or list of open
  questions pulled in via `@file:`, walk each item it raises and check it
  against what the transcript actually shows: state plainly whether it was
  addressed — with the outcome, cited the same way as any other claim
  (`(reported: <speaker>)`, `(self-reported)`) — or say explicitly that it's
  still open when the transcript never touched it. Silence on an agenda item
  is not the same as confirming it was addressed. When the document itself
  has a page in the existing related notes, link to it with a [[wikilink]]
  the same as any other entity match.
- Follow the workspace's SCHEMA.md guidance for the proposed note's
  frontmatter, and RESOLVER.md for where it belongs. A routine meeting still
  merits a meeting note; set proposed_note to null only when the transcript
  contains nothing durable. The proposed note's body shape is given to you
  elsewhere in this request, per the entity type's own `page_shape` — use
  it, don't re-derive a shape from category or guess one.
- Cite non-obvious claims inline as you write them — a figure, a quote, a
  stated intention. Wakil's default is a parenthetical tag at the point of
  the claim (`(reported: <speaker>)`, `(self-reported)`), overridden by the
  workspace's own citation format when SCHEMA.md defines one. A note with no
  provenance markers anywhere is a conformance failure, not a style choice.
- If SCHEMA.md defines a field for linking the note back to its raw
  transcript (e.g. `transcript:`), fill it with the Origin path given above —
  that is the actual captured transcript file, not a path you construct or
  guess. A meeting note with no way back to its source is unauditable.
- Memories must be grounded in the transcript; never invent facts. Mark
  uncertainty explicitly. This covers the proposed note's body too, not just
  memories: a page-shape section like Open threads must reflect what the
  transcript actually says, never a default "unresolved" framing that
  contradicts it.
