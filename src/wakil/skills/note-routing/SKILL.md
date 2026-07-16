---
name: note-routing
description: Decides the destination directory and filename for a note using entity schema.directory values and the workspace's RESOLVER.md; use whenever a new or moved file needs a home before it is written.
skill_api: 1
---

# note-routing

You are the routing step. Given a piece of content that's about to become a
file in the knowledge base — a raw source, a synthesized note, a journal
entry, a meeting record, or a resolved entity — decide **where it goes**:
which directory, and what filename.

That is the whole job. You do not decide the page's internal shape,
frontmatter fields, or heading structure — that's `note-conformance`'s job.
Keep the boundary explicit:

```text
RESOLVER.md → where the note belongs   (this skill)
SCHEMA.md   → how the note is shaped   (note-conformance)
```

Don't reach across it. If you catch yourself drafting frontmatter or
deciding section headings, stop — route first, hand off, let
`note-conformance` do the shaping.

## Two routing authorities, and when each applies

Routing has exactly two sources of truth. Never invent a third.

1. **Entity types are code-owned.** If the content resolves to an entity
   `entity-resolution`/`entity-enrichment` already identified — a person, a
   company, a concept, whatever entity schemas this wakil installation
   defines — its destination directory is `schema.directory` for that
   entity's `type`, as declared in the schema itself (see
   `src/wakil/schema/entities/*.yaml`, `directory:` field). This is not a
   judgment call. Don't reason about it, don't override it, don't propose an
   alternative directory because it "feels" more fitting. If the type is
   unknown to the schema loader, that's a hard stop upstream (entity
   resolution refuses to guess), not something to route around here.

2. **Everything else is workspace-owned, via `RESOLVER.md`.** Raw sources,
   synthesized notes, journal entries, meeting records, and anything else
   that isn't a single resolved entity are not wakil's business to place —
   they're the knowledge base's own convention. `RESOLVER.md` is one of
   wakil's recognized special files (`SPECIAL_FILES` in
   `src/wakil/config/settings.py`); when the workspace has one, it is the
   **sole authority** for this class of routing decision. Read it, follow
   its decision order, and don't second-guess it with judgment imported from
   somewhere else.

   **If the workspace has no `RESOLVER.md`, or it doesn't cleanly cover the
   case in front of you, do not guess a destination. Ask the user.** A wrong
   guess here is a misfiled, hard-to-find note; asking costs one question.

## Filing principle: primary subject, not format or source

When `RESOLVER.md` routes by subject-matter directories (as opposed to a
flat "everything of this type goes here" rule), file by the content's
**primary subject** — what someone would search for to find this page again
— never by its format or where it came from.

- An article *about* a company belongs wherever the workspace routes company
  material, not in a generic "articles" bucket, because it came from a web
  clipping.
- A meeting transcript that's substantially about one project belongs with
  that project, not filed only under "meetings," because a transcript is a
  format, not a subject.
- Don't file a document under a workspace's raw-import bucket just because
  ingestion happened to produce it there; if it has a clear primary subject,
  route it to that subject's home per `RESOLVER.md`.

**Exception — sui generis synthesis.** Some synthesized output is
one-of-one to a single source *and* a specific reading of it (a
personalized walkthrough of one document, a synthesis tied to one narrow
problem) and genuinely doesn't fit any subject directory: filing it by topic
loses the "this is a synthesis of that one thing" dimension, and filing it
by subject muddles it with the subject's own canonical material. Route this
narrow case only if the workspace's `RESOLVER.md` actually defines a home
for it — don't invent a synthesis-output directory that isn't backed by the
workspace's own convention.

## Notability gate

This applies whenever routing implies creating a new entity page (as
opposed to filing content that references an existing one):

> Before creating a new entity page, check notability: will you interact
> with this person again, or are they relevant to your work/interests? Is
> the company relevant to work, job-search, or interests? Is the concept a
> reusable mental model worth referencing again? When in doubt, don't
> create — a missing page can be created later, but a junk page wastes
> attention and degrades search quality.

In practice this gate is `entity-resolution`/`entity-enrichment`'s call to
make before routing is even asked to place a new file — but if you find
yourself about to route a create for something that never cleared this bar,
that's a signal to send it back rather than file it.

## Decision tree

- [ ] Step 1: Check whether this content is a single resolved entity (the
      output of `entity-resolution`/`entity-enrichment`, with a known
      `type`). If it is, its destination is `schema.directory` for that
      type — done, this is deterministic, not a judgment call.
- [ ] Step 2: Otherwise, check whether the workspace has a `RESOLVER.md`.

      **If the workspace has no `RESOLVER.md`, or it doesn't cover this
      case, this step is a conditional exit, not a checkbox to clear and
      move past: stop and ask the user, and do not guess.** Only proceed to
      Step 3 once you've confirmed a `RESOLVER.md` exists and covers the
      case in front of you.
- [ ] Step 3: Walk `RESOLVER.md`'s own decision order for the content in
      front of you (it may route by sensitivity, subject type, or another
      scheme entirely — follow *its* order, not a generic one).
- [ ] Step 4: Within a subject-matter directory, file by primary subject,
      not format or source (see above). Apply the sui-generis-synthesis
      exception only if `RESOLVER.md` itself defines a home for that case.
- [ ] Step 5: Generate the filename using the workspace's own slug/naming
      convention as stated in `RESOLVER.md` (or, absent one, ask rather
      than invent a convention).
- [ ] Step 6: When the routing decision isn't obvious — genuinely fits two
      destinations, or the workspace's rules underdetermine it — surface
      the ambiguity and your reasoning rather than silently picking one.

## Hard rule

Never assume a destination directory that isn't backed by either a real
entity `schema.directory` value or an explicit rule in the workspace's own
`RESOLVER.md`. A plausible-sounding directory name you haven't verified
against one of those two sources is a guess, not a routing decision.
