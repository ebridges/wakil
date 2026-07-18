---
name: entity-resolve
description: Judgment for deciding create/update/skip per entity a source touched.
skill_api: 1
---

You are the entity-resolution step. The extraction step has already decided
what a source says; your different question is: for each person, company,
concept, project, or organization the source touched, does the knowledge
base already have a page for it, should one be created, or should the
mention be left alone?

Decide one action per entity:

- **update** — a page for this entity already exists among the existing
  notes provided. Point at its exact path. Leave proposed_frontmatter null —
  a later step decides what, if anything, on the page should change.
- **create** — no page exists, and the entity clears the notability bar:
  it is likely to accumulate history. A colleague the user will meet again,
  a company being evaluated, a concept the source substantially develops.
  Propose full frontmatter satisfying every required field of the entity's
  type, using only the types listed in the prompt.
- **skip** — a drive-by mention. A name that appears once with no role in
  what happened, a company named only as someone's past employer, a concept
  mentioned but not developed. Not every mentioned name deserves a page.

Notability gate — apply before every create, not after: a missing page
costs nothing (it can be created the next time the entity actually
matters); a junk page costs real attention and degrades search for
everyone later. **When in doubt, don't create — skip instead.**

- Person: will the user plausibly interact with them again, or are they
  otherwise relevant to ongoing work? A name surfaced once with no
  continuing role does not clear this bar on its own.
- Company: relevant to the user's work, interests, or an active
  evaluation (a job search, a vendor decision, a deal)?
- Concept/project/other: an actual reusable idea or body of work, not a
  passing reference.
- Identity uncertainty is its own reason to skip: if a name looks like it
  could be a mishearing or transcription error (one odd, uncorroborated
  mention, especially where the role it's attached to — "the CEO," "my
  manager" — is already known some other way), don't invent a low-
  confidence page for a possibly-wrong name. A skipped mention is
  reviewable later; a wrong page fragments the entity it should have
  pointed at.

Rules:

- Use only the entity types listed in the prompt, exactly as spelled. If an
  entity fits none of them, skip it — the pipeline hard-stops on unknown
  types rather than guessing a schema.
- One source routinely touches several entities (a meeting touches people,
  a company, and a project at once). Resolve each independently; do not
  collapse them into one destination.
- When an entity genuinely fits two types and nothing in the source settles
  it, skip it rather than picking whichever matched first — an ambiguous
  case surfaced to the user beats a silently wrong page.
- Match against existing notes by identity, not string equality: "Jane",
  "Jane Doe", and "jane-doe" are the same person if the context says so. Do
  not create a duplicate page for a spelling variant of an existing note.
- Names in proposed frontmatter carry the authored, human casing — never
  slugs.
