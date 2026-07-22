---
name: entity-enrichment
description: Decide which existing entities a note should link to, propose creating entities that clear the notability gate, and add texture — beliefs, motivations, trajectory — rather than a bare fact sheet. Use after entity-resolution has matched a note's mentions, whenever you're deciding how a note connects to the knowledge base.
skill_api: 1
---

# entity-enrichment

`entity-resolution` answers "does this mention correspond to an existing
page, and does it warrant one?" This skill picks up from there and answers
the next question: **given those resolution results, how should the note
actually connect?**

```text
entity-resolution → determine what an entity refers to
entity-enrichment → decide how the note should connect to it
```

If you're checking whether "Jane" is `people/jane-doe.md`, that's
`entity-resolution`. If you're deciding whether this mention of Jane earns a
back-link, whether her page's State (Compiled Truth) needs updating, or
whether a brand-new page for her is worth creating at all — that's here.
Never skip straight to linking or creating without resolving identity
first; this skill consumes `entity-resolution`'s output, it doesn't
re-derive it.

## When to use

- Whenever a note or source references a person, company, project, or
  concept and `entity-resolution` has already produced a match, a create
  candidate, or an ambiguous result for it — during `ingest-source`,
  `note-revision`, or any manual edit that touches entity mentions.
- Anywhere you're about to add a `[[wikilink]]`, fill in a page's texture
  sections, or propose a new entity page and need to decide whether it's
  worth doing.

## Where wakil's own pipeline stops — and why this skill exists

`wakil enrich <source-id>` already runs entity resolution and, for every
`action: create` result, writes a schema-valid stub
(`_build_stub_entities`, `src/wakil/app/ingest_service.py`): frontmatter
carrying only what the CLI's internal resolution DAG step proposed plus
`created`/`updated`, and a body skeleton of `## Compiled Truth`,
`## Open Threads`, and `## Timeline / Log` (`_stub_content`) — empty,
waiting to be filled. That's identity resolution and page scaffolding, not
enrichment. The CLI path does not decide which of the source's *other*
mentions deserve a back-link, does not write anything into State (Compiled
Truth), and does not add Timeline entries beyond what extraction directly
proposed.

That judgment is this skill's job — applied to a freshly-stubbed page, an
existing page being updated with new signal, or a mention encountered during
`note-revision` or manual editing, anywhere outside the CLI's fixed DAG.

## Procedure

For each entity `entity-resolution` returned for the note in hand:

1. **Confident match.** Decide whether this specific mention earns a
   back-link (see "Avoid superficial linking" below), and whether the
   mention carries signal material enough to update the matched page's
   Compiled Truth or add a Timeline entry. Not every match needs a page
   edit — a passing reference that adds no new fact just gets the link.
2. **Create candidate (cleared the notability gate).** Hand the page to
   `note-routing` for its destination directory and filename (entity
   directories come from `schema.directory` — `people/` for `person`,
   `companies/` for `company` — never invent one), then write real content:
   Compiled Truth with texture, not `[No data yet]` boilerplate, plus a
   first Timeline entry. `note-conformance` is the pass that checks the
   result is schema-valid before it's committed — this skill decides
   content, not shape.
3. **No match, not notable, or ambiguous.** Don't force a link and don't
   create a page. See "Preserve ambiguity" below.

## Scale effort to importance

Not every entity deserves the same investment. Before writing anything,
weigh how much this entity matters to the user's ongoing work: a close,
recurring contact or a company under active evaluation earns a thorough
pass — real research into what's changed, a full texture rewrite if
material. A name that surfaces once with light but genuine relevance earns
a light pass — a Timeline entry and a link, nothing more. Don't spend the
same effort tracking down background on someone who appears once in passing
as you would on someone the user works with weekly; effort is a dial, not a
fixed procedure.

## Texture over facts

A page's Compiled Truth should read like an intelligence dossier, not a
scraped fact sheet. Hard facts (role, company, status) are table stakes —
what makes a page worth having is texture: what does this person believe,
what are they building, what's driving them, where is their trajectory
headed. When updating an existing page, re-synthesize Compiled Truth to
cover the union of what was already known plus the new signal — never
replace it with only what the newest source said, and treat "my rewritten
section is shorter than what was there" as a stop-and-check signal, not
confirmation you're done. Timeline entries are append-only: add dated
entries, never delete or reorder what's already there, including
auto-generated back-link lines.

## Send existing knowledge as context, not a rehash

Before writing new content into a page, check what the knowledge base
already knows about the entity — this is exactly what `prepare_enrichment`
already does for you in the CLI path (`search_workspace` builds
`related_notes`, threaded into both the extraction and resolution prompts
so the model reasons from what's already known rather than from nothing).
When working by hand outside that path, do the same thing manually: search
for the entity's existing page and related mentions first, then let the new
source contribute only the delta — what's new or what contradicts prior
Compiled Truth — instead of re-deriving the whole page from the one source
in front of you.

## Mandatory author-page creation

Anyone whose thinking is worth ingesting is worth tracking. When a note
captures someone's original thinking — an article, an essay, a tweet, a
standalone idea — the author gets a page if `entity-resolution` doesn't
already find one, regardless of how minor their other mentions in the piece
would otherwise read. This is a standing exception to the general
notability gate, not a bypass of resolution: still resolve the author's
identity first to check for an existing page, but the act of ingesting
their original thinking is itself the notability signal — a person whose
thinking justified capturing an entire note is not a drive-by mention.

## Avoid superficial or excessive linking

A back-link earns its place when the mention is substantive — the note says
something about the entity, not just that the entity was in the room. Don't
wikilink every incidental name-drop; a page whose every sentence is
studded with links is harder to read, not more connected. When a matched
entity has its own page, still create the back-link from that entity's page
to this one — an unlinked substantive mention is a broken connection — but
apply the same judgment in both directions: link what matters, skip what's
incidental.

That "is it substantive enough" judgment call is for matches
`entity-resolution` surfaced by search. A related note the user pointed at
explicitly — reached through an `@file:` reference in the source's context,
not just found by `search_workspace` — carries a different signal: the user
picked that note deliberately, so treat it as high-confidence and back-link
it rather than weighing the mention against the substantive bar above.

That confidence is about the link decision only — it says nothing about
content-worthiness. Whether the mention also earns a Compiled Truth edit or
a Timeline entry is still governed by the ordinary test in "Confident
match" above: does it carry a new fact, development, or contradiction? An
explicit `@file:` reference to a page that's mentioned only in passing —
nothing new said about it — still gets just the link, exactly as any other
passing reference would.

## Preserve ambiguity

Don't resolve an ambiguous or no-match case by picking the closest-looking
candidate, forcing a link anyway, or merging it into an existing page that's
only a rough fit. This is the same discipline `entity-resolution` applies at
the identity layer — carry it forward here: an unresolved mention left
unlinked is recoverable later; a wrong link or a bad merge corrupts two
pages' history and is far harder to undo. When in doubt, leave the mention
unlinked and say so rather than guessing.

## Where the output goes next

This skill decides content — which links, which pages, what goes in
Compiled Truth and Timeline. It does not decide destination directories or
filenames (`note-routing`'s job) and does not do the final schema/prose
audit (`note-conformance`'s job, including the `name:`/`title:` category
rule and slug consistency). Hand off to both before anything gets
committed.
