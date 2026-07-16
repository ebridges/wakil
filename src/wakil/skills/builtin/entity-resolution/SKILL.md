---
name: entity-resolution
description: Match a person, company, project, or concept a source or note refers to against existing knowledge-base entities — confident match, no match, or ambiguous. Use before adding a link, a stub page, or a frontmatter reference to any entity.
skill_api: 1
---

# entity-resolution

This skill answers one narrow question: **does this referenced entity
correspond to an existing knowledge-base page, and if not, does it warrant
one?** It does not decide which links a note should carry, how many, or
where they go — that judgment belongs to `entity-enrichment`. Keep the two
separate:

```text
entity-resolution → determine what an entity refers to
entity-enrichment → decide how the note should connect to it
```

If you're asking "what page is this?", you're in this skill. If you're
asking "should this note link to it, and how?", you're past this skill's
scope — hand the resolution result to `entity-enrichment`.

## When to use

- Any time a note, source, or edit mentions a person, company, project, or
  concept and you need to know whether it already has a page before
  proposing a link, a stub, or a frontmatter `ref` field.
- Most directly during `note-revision` and manual editing — anywhere an
  agent is touching a note by hand, outside `wakil enrich`.

## The CLI path already does this — don't duplicate it

`wakil enrich <source-id>` runs entity resolution as a fixed DAG step,
always invoked, never optional (`src/wakil/app/ingest_service.py`,
`_run_entity_resolution`) — `ingest-source` documents how that internal DAG
step fits into the pipeline. It applies the same judgment against every
entity the extraction step surfaced, then `_build_stub_entities` turns each
`action: create` result into a schema-routed stub page, and
`validate_proposal()` hard-stops on any entity type the schema doesn't
recognize rather than best-guessing a shape. If you're driving ingestion
through `wakil ingest` / `wakil enrich`, that mechanism already applies this
judgment — don't re-run it by hand.

This skill exists for everywhere else: `note-revision`, ad hoc editing, or
any other case where an agent is resolving an entity mention outside that
pipeline. The judgment below is the same one the CLI's internal DAG step
encodes — apply it consistently so a note edited by hand and a note
produced by `wakil enrich` don't disagree about whether "Jane" is
`people/jane-doe.md`.

## Decision procedure

For each entity a source or note touches, resolve independently — one
transcript or note routinely touches several entities (a person, their
company, a project) at once; don't collapse them into one destination.
Reach exactly one of three outcomes:

- **Confident match.** An existing page corresponds to this entity. Match
  by identity, not string equality: "Jane," "Jane Doe," and a stored
  `people/jane-doe.md` with `aliases: [Jane]` are the same person if
  context supports it. Point at the page's exact path; do not restate or
  guess at its frontmatter.
- **No match, and it clears the notability gate.** No page exists yet, but
  the entity is worth one — see below. Surface this as a create candidate
  for `entity-enrichment` to act on; don't create the page yourself as a
  side effect of resolving identity.
- **No match, and it doesn't clear the gate, or it's genuinely
  ambiguous.** A drive-by mention, or a name that fits two existing pages
  (or two entity types) with nothing in the source to settle it. Surface
  it as unresolved rather than guessing.

## Notability gate

Before treating an unmatched entity as a create candidate, apply the same
bar the CLI's internal DAG step uses: will you interact with this person again, or are
they relevant to your work or interests? Is the company relevant to work,
job search, or interests? Is the concept a reusable mental model worth
referencing again? A page clears the gate when it's likely to accumulate
history — a colleague the user will meet again, a company under evaluation,
a concept the source substantially develops. It doesn't clear the gate when
it's a drive-by mention — a name appearing once with no role in what
happened, a company named only as someone's past employer, a concept
mentioned but not developed.

When in doubt, don't propose creation. A missing page can be created later
once the entity actually recurs; a junk page sits in every future search
result and degrades retrieval for everything else.

## Holder ≠ subject

When the source or note attributes a claim to someone, the entity you're
resolving as the claim's holder is whoever said or clearly implied it, not
whoever the claim is about. A founder describing their own company's
prospects is the holder — companies don't speak, their employees do. If
you're resolving "who is this claim attributed to" as part of matching, do
not resolve it to the company just because the company is the claim's
subject; resolve it to the person who made the statement, and let the
company be a separate entity if it's independently referenced.

## Never auto-merge

Do not resolve an ambiguous case by picking whichever candidate matched
first, and do not merge two existing pages into one because they look
similar. An entity that genuinely fits two existing pages, or fits none
cleanly, is more useful surfaced as ambiguous than silently collapsed into
a wrong match — a bad merge is harder to undo than an unresolved mention,
because it corrupts both pages' history. Preserve the ambiguity and let
`entity-enrichment` or the calling agent decide how to handle it (skip the
link, ask, or leave a flagged note).
