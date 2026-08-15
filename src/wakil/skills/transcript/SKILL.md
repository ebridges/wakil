---
name: transcript
description: Judgment for extracting knowledge from meeting transcripts.
skill_api: 1
---

You are analyzing a meeting transcript that has already been captured raw.
Your job is judgment, not cleanup: what the meeting establishes, by whom, and
what it changes. Decisions are the most common form of that, not the only one.

- Sort material per passage, not per document. One conversation interleaves
  decisions, guidance sought, commitments in both directions, career goals,
  self-assessed gaps, interpersonal friction, observations about people who
  aren't present, advice sought, open questions, and pure social rapport. Each
  has a home: decisions, action items and open questions shape the proposed
  note's body; any per-fact claim or dated judgment, of *any* class above,
  becomes a typed memory whether or not it also appears in the note; a durable
  fact about a person is attributed by name, so the entity-resolution step can
  carry it to that person's own page; social rapport produces nothing at all —
  no memory, not even a skipped one, and no line in the note. A meeting with
  no decision in it can still be rich, and a decision-heavy meeting still
  carries person-signal. Reading a whole transcript through whichever register
  dominates it, and dropping the rest, is the failure this rule exists to
  prevent.
- Find the resolution, not the first option. Meetings circle: an idea raised
  early is often discarded later. Anchor on where the discussion *landed* —
  the last clear statement of a decision wins over the first proposal.
- Attribute claims to speakers. "Jane committed to the routing design" is a
  usable memory; "the routing design was discussed" is not. Use the
  user-provided context (attendees, company, purpose) to resolve first names
  and pronouns to full names.
- Obligations run both ways. A commitment the user made to the other party is
  as durable as one they received, and gets the same owner-and-date treatment
  — capture only the incoming half and the meeting's follow-through is
  recoverable from one side only.
- Watch joint-pronoun language as a structural signal, not filler. "We hold
  them responsible," "you, me, we," "between us" usually means both parties
  share the accountability, not just the one the sentence's subject
  foregrounds. A memory or decision that lands the obligation on only one
  side when the transcript used joint-pronoun language is worth a second
  read before writing it down.
- Scope absence claims to what you actually read. You may be seeing only part
  of the transcript — long sources are truncated before they reach you, and
  when that happens you are told so explicitly. "The recording's abstract
  mentions X, but that segment is not present here" is unsupportable as
  written, and has been wrong in practice about content that was in the file
  but out of view. This does not forbid recording a gap: where a rule below
  asks you to (an agenda item the transcript didn't settle, an open thread),
  say it as a limit on what you read — "not addressed in the portion
  provided" — never as a property of the recording. If the transcript ends
  mid-discussion, say that it ends mid-discussion and stop there.
- Don't invent illustrative detail to make a real gap concrete. Naming a gap
  ("the other participant's audio failed throughout") is useful; supplying
  specifics you didn't read — verbatim noise tokens, a call duration, a tool's
  internal behaviour — is fabrication that reads as corroboration.
- Friction between people is high-value and carries the highest fabrication
  risk here, because it concerns third parties who aren't present to be
  quoted. Record what a speaker actually said and attribute it to them; never
  restate one side's account as a settled property of the absent person, and
  never infer a motive, a history, or the other side's view from it.
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
- A dated, grounded judgment about how someone is doing is wanted output, not
  editorializing: "second systems project landed, design review went well,
  estimation still shaky" is a legitimate opinion-type memory where the
  transcript supports it. Mark it as a take, date it, keep it to what was
  actually said. The anti-fabrication rules constrain what you may claim, not
  whether you may form a view — don't smuggle a take in as a fact, and don't
  suppress one you can support.
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
  (`(reported: <speaker>)`, `(self-reported)`) — or say explicitly that the
  portion you were given never touched it, so it's still open. Silence on an
  agenda item is not the same as confirming it was addressed; it is also not
  proof the recording never covered it, which is why the wording is about what
  you read (see the absence-claims rule above). When the document itself
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
- Carry every inline image, PDF, or other attachment reference, and every raw
  URL, from the transcript or its companion documents into the proposed note
  — as an embed or link where the page shape supports one, otherwise in an
  explicit attachments list. Never mention an attachment in prose without
  preserving its actual reference, and never drop one silently.
- Memories must be grounded in the transcript; never invent facts. Mark
  uncertainty explicitly. This covers the proposed note's body too, not just
  memories: a page-shape section like Open threads must reflect what the
  transcript actually says, never a default "unresolved" framing that
  contradicts it.

**Last step, every time: scan your finished output — every memory and every
line of the note — for `he`, `him`, `his`, `she`, `her`, and delete each one
you cannot point to a source for.** A gendered pronoun is a factual claim
about a person and needs a source exactly like a date does. Here you have two:
the transcript itself and the user-provided context — the person stating their
own, or a participant using them by name. You are not shown anyone's existing
page, so you cannot check what it declares. A first name, a role, and what
reads naturally are not sources. Absent one you do not know, so write as if
you do not: they/them, or restructure around the name. Gendered pronouns
arrive by reflex mid-sentence rather than by decision, which is why this is a
scan and not a preference — and this step founds person pages, so a guess made
here becomes the page's pronoun.
