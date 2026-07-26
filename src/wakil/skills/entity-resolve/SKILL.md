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

## Relevance: how much does the source actually concern this entity?

Separate from confidence (below), every entity you resolve also gets a
`relevance` judgment: how much *this particular source* concerns them —
not how important they are in general, and not how easy they were to
resolve. This matters most for action=update: it decides whether the
entity's page is worth the cost of a full revision pass later, so judge
it from what the source actually does with the entity.

- **central** — a primary subject of the source, or a participant in it.
- **notable** — a real stakeholder in what's discussed, even if they're
  not personally discussed at length (e.g. one of several people a
  decision affects, named but never individually talked about).
- **minor** — mentioned with some context, but not a focus of the source.
- **peripheral** — named only as background. The source isn't really
  about them, even if the mention is substantively true.

Worked example, from a planning call between two participants scoping a
consulting engagement: the two participants and the project being scoped
are **central**. Colleagues named as affected by the outcome but never
personally discussed are **notable**. A colleague named in passing with a
caveat ("short tenure, no read on him yet") is **minor**. A company named
only as *"the reason I only have two weeks free"* is **peripheral** — it
explains a constraint the other side cares about, but the source isn't
about that company. Conflating "this explains something important" with
"this entity is relevant" is the mistake to avoid.

## Confidence: is this the right page?

`confidence` is a different question — identity-match certainty, not
relevance. A distinctly-named, unambiguous entity resolves with high
confidence even when it's barely mentioned (peripheral + high confidence
is a normal combination, not a contradiction); a common or ambiguous name
gets lower confidence even when it's central to the source.
