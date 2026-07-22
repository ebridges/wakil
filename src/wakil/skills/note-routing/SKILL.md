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

## Default judgment patterns for hard filing calls

`RESOLVER.md`, when present, is the authority for a workspace's specific
vocabulary — which subject directories exist, what counts as sensitive, its
own hard calls. Two judgment *patterns* recur often enough across knowledge
bases that they're worth carrying as a default reasoning template. Apply
them only when the workspace's own `RESOLVER.md` doesn't already settle the
case in front of you — a workspace's own answer always wins over the
default.

**Instructional/timeless vs. productive/time-bound.** Many workspaces
distinguish a concept-like type (a reusable framework or mental model, no
owner, no due date, transferable across contexts) from a project-like type
(a specific, owned, time-bound effort with a status and an execution log).
When content mixes both — a thesis plus its own execution log — split it:
route the timeless part to the concept-like home, the dated execution
tracking to the project-like home, rather than forcing one artifact to serve
both.

**Raw vs. synthesized.** A capture that hasn't been interpreted yet (a
transcript, a clipping — exactly what `wakil ingest` writes under
`sources/`) and a note that synthesizes what it means are different
artifacts even when they're about the same event. Don't route interpretive
content into a `source`-type location just because that's where the raw
material landed, and don't leave raw material uninterpreted in a directory
meant for synthesis.

## Sensitive content

If a workspace routes anything to a directory or entity type it flags as
sensitive (a schema's `sensitive: true` field, or `RESOLVER.md`'s own
sensitive-content section), treat that routing decision itself as sensitive:
don't surface its content in summaries, exports, or shared context without
being explicitly asked. This extends the working agreement every wakil
session already operates under to the routing decision itself, not just
downstream handling of the resulting note.

## Naming defaults

Absent a workspace-specific naming convention in `RESOLVER.md`, use:

- lowercase kebab-case for every filename (`slugify()`,
  `src/wakil/app/ingest_service.py`, is the reference implementation — see
  `note-conformance`'s "Slug consistency");
- a qualifier suffix to disambiguate two entities that would otherwise
  collide on the same slug (`david-liu-acme.md`, `david-liu-example.md`),
  rather than picking one arbitrarily or silently overwriting;
- a leading ISO date (`YYYY-MM-DD-`) for slugs naming a specific dated
  occurrence — a meeting, a journal entry — not for identity- or
  concept-type slugs, which name a subject rather than an occurrence.

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
      For a hard call `RESOLVER.md` doesn't resolve directly, check "Default
      judgment patterns" above before asking.
- [ ] Step 5: Before generating a filename, confirm Step 4 actually landed on
      one destination directory. When it didn't — the content genuinely fits
      two destinations, or the workspace's rules underdetermine it — stop
      here: surface the ambiguity and your reasoning rather than silently
      picking one, and do not generate a filename/slug at all, since it
      depends on a directory that isn't settled yet.
- [ ] Step 6: Once the directory is actually settled, generate the filename
      using the workspace's own slug/naming convention as stated in
      `RESOLVER.md`, or the "Naming defaults" above when it doesn't specify
      one.

## Hard rule

Never assume a destination directory that isn't backed by either a real
entity `schema.directory` value or an explicit rule in the workspace's own
`RESOLVER.md`. A plausible-sounding directory name you haven't verified
against one of those two sources is a guess, not a routing decision.
