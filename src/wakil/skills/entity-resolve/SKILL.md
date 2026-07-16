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
  notes provided. Point at its exact path. Propose in proposed_frontmatter
  only the fields that should change (say, a new role or status); never
  restate the whole frontmatter.
- **create** — no page exists, and the entity clears the notability bar:
  it is likely to accumulate history. A colleague the user will meet again,
  a company being evaluated, a concept the source substantially develops.
  Propose full frontmatter satisfying every required field of the entity's
  type, using only the types listed in the prompt.
- **skip** — a drive-by mention. A name that appears once with no role in
  what happened, a company named only as someone's past employer, a concept
  mentioned but not developed. Not every mentioned name deserves a page.

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
