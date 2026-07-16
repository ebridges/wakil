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
- Separate decisions from open questions. A decision has an owner and a
  next step; anything still contested belongs in a question-type memory, not
  a decision.
- Date what happened. Memories describing dated events (the meeting itself,
  a committed deadline, an announced change) are event-type memories carrying
  the event's own date — not the date you are writing this.
- Quote pivotal exchanges verbatim in the proposed note where the exact
  wording matters (commitments, disagreements, numbers); paraphrase the rest.
- Link entities — people, companies, concepts, projects, prior meetings — to
  the existing related notes with [[wikilinks]] where they genuinely match;
  mention entities without a matching note in plain text and leave their
  page-creation decision to the entity-resolution step.
- Follow the workspace's SCHEMA.md guidance for the proposed note's
  frontmatter and page shape, and RESOLVER.md for where it belongs. A routine
  meeting still merits a meeting note; set proposed_note to null only when
  the transcript contains nothing durable.
- Memories must be grounded in the transcript; never invent facts. Mark
  uncertainty explicitly.
