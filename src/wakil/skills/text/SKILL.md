---
name: text
description: Judgment for extracting knowledge from plain text clippings.
skill_api: 1
---

You are analyzing a captured text clipping — pasted notes, an exported
thread, a fragment whose shape is not known in advance. Judge the shape
first, then extract accordingly.

- Identify what this is before extracting: notes someone typed, a quoted
  exchange, a list, a draft. The user-provided context is often the only
  clue to who wrote it and why it was kept.
- Preserve the author's voice where the wording is the point (a phrasing
  worth keeping, a commitment, a definition); summarize where it is not.
- Extract claims at the granularity they were made. Do not merge unrelated
  fragments into one memory, and do not split a single argument into
  disconnected pieces.
- Date what is dated: if the clipping records something that happened on a
  known date, that memory is an event carrying the event's own date.
- Link entities — people, companies, concepts, projects — to the existing
  related notes with [[wikilinks]] where they genuinely match; mention
  entities without a matching note in plain text and leave their
  page-creation decision to the entity-resolution step.
- Follow the workspace's SCHEMA.md guidance for the proposed note's
  frontmatter and page shape, and RESOLVER.md for where it belongs. Much
  clipped text is only worth its raw capture — set proposed_note to null
  rather than manufacturing a durable note.
- If SCHEMA.md defines a field for linking the note back to its raw capture,
  fill it with the Origin path given above, not a guessed or reconstructed
  path.
- Cite non-obvious claims inline as you write them. Wakil's default is a
  parenthetical tag at the point of the claim (`(self-reported)`,
  `(reported: <source>)`), overridden by the workspace's own citation format
  when SCHEMA.md defines one.
- Memories must be grounded in the clipping; never invent facts. Mark
  uncertainty explicitly. This covers the proposed note's body too, not just
  memories: a page-shape section like Open threads must reflect what the
  clipping actually says, never a default "unresolved" framing that
  contradicts it.
