---
name: note-revision
description: Merge new information into an existing note without losing what it already says — classify the change, re-synthesize State, append to Timeline, and preview before writing. Use whenever new facts about an already-noted person, company, project, or topic need to land on its existing page.
skill_api: 1
---

# note-revision

`content-synthesis` writes a new note from scratch. `note-conformance` checks
a note's shape once it's written. `note-revision` is the step in between,
for the case those two don't cover: a note **already exists**, and new
information — from `entity-enrichment`, `knowledge-research`, a fresh
source, or a user correction — needs to land on it without erasing what's
already there. Losing previously-captured knowledge while "updating" a page
is the single worst failure mode this skill exists to prevent.

## When to use

- New facts about a person, company, project, or topic arrive and a note
  for that subject already exists.
- Explicitly asked to update, revise, or merge new information into an
  existing note.
- An attached companion document (a prep note, an agenda) itself needs
  updating after the fact — e.g. checking off its own open questions in
  place once a meeting resolved them. `transcript` only writes that
  resolution into the *new* meeting note; updating the companion document
  itself is a merge into an existing note, which is this skill's job.
- **Not** for creating a note that doesn't exist yet — that's
  `content-synthesis`. Not for fixing frontmatter/slug/link shape with no
  new information to merge — that's `note-conformance`. If a revision also
  needs a fresh routing decision (the subject moved directories, a new
  entity was split out), hand that off to `note-routing`; don't decide it
  here. And don't assume a newly-mentioned entity (a spun-out company, a
  person who wasn't tracked before) clears the notability gate just because
  it's related to the note you're revising — that judgment belongs to
  `entity-resolution`; name it rather than deciding notability yourself.

## Procedure

- [ ] Step 1: **Read the existing note in full before writing anything.**
      Load its current State, Timeline, and frontmatter. You cannot
      classify a change as additive, contradicting, or duplicate against
      content you haven't read.
- [ ] Step 2: **Re-read the raw source for the new facts, not your memory
      of it.** See "Extraction integrity" below — never transcribe a
      figure, date, or quote from earlier in a conversation when the saved
      raw source is available to re-read at the moment of writing.
- [ ] Step 3: **Classify the new material** against what's already on the
      page: additive, contradicting/superseding, or a duplicate. See "The
      three-tier dedup heuristic" below.
- [ ] Step 4: **Apply the region-specific update discipline**:
      re-synthesize State, append to Timeline. Never regenerate the whole
      page from only the new source. See "State vs. Timeline" below. Carry
      forward every attachment/URL reference the same way — see "Attachment
      and raw URL fidelity" below.
- [ ] Step 5: **Scan your draft for `he`, `him`, `his`, `she`, `her`.**
      Rewrite any that aren't licensed by the subject's own `pronouns:`/
      `gender:` field or by an explicit statement in the source. See
      "Pronouns: don't infer them" below.
- [ ] Step 6: **Diff your draft against the current page before saving.**
      If the new version is shorter, or drops a fact/frontmatter key that
      was there before, stop — you are very likely clobbering, not
      updating. See "The clobbering bug" below.
- [ ] Step 7: **Preview, don't auto-apply.** Surface the diff for
      confirmation before writing, the same as every other wakil write
      path. See "Preview before writing" below.
- [ ] Step 8: **Update provenance**, including the `updated:` frontmatter
      field, and hand off to `note-conformance` for a shape check before
      `kb-commit`.

## State vs. Timeline: two disciplines, one page

A knowledge page has two regions with opposite update disciplines. Get this
backwards and you clobber the page:

- **State (Compiled Truth)** is *re-synthesized* on every update to cover
  the union of what was already there plus the new facts. "Rewrite the
  State section" means re-derive it from everything known, never replace it
  with only the newest source.
- **Timeline (Log)** is *append-only*. New dated entries are added; existing
  entries — including auto-generated back-link lines — are never deleted,
  rewritten, or reordered.

Losing a previously synthesized fact is a regression, not an update.

## The clobbering bug

A cautionary case from a prior system, in outline: three related pages (a
company, a person, and a program) were each fully regenerated from a single
new transcript instead of merged into their existing content. "Rewrite the
State section" was misread as "replace the page with the newest source,"
and the regeneration silently deleted frontmatter, prior distilled history,
and Timeline entries that didn't happen to appear in that one transcript.

The lesson: full-file regeneration is the *mechanism* that enables
clobbering. Prefer surgical edits — append a Timeline entry, edit the State
block — over rewriting an existing page wholesale. Treat "my new version is
shorter than the old one" as a stop-and-check signal, not something to wave
through.

## Attachment and raw URL fidelity

Re-synthesizing State must carry forward every inline image, PDF, or other
attachment reference, and every raw URL — whether it's already on the note
or newly present in the source being merged — the same as it carries
forward any other fact. Collapsing an existing `![[...]]` embed, or a
source's inline attachment, into a prose sentence while writing the new
State is the clobbering bug in miniature: the result reads as "cleaner" and
is in fact a regression. As with any other fact, only attachments and URLs
referenced inline in the source's own text are in scope for this
carrying-forward duty; a sibling file sitting in the source's folder that
the text itself never mentions is a known, out-of-scope gap for now (see
`content-synthesis`'s own note on this boundary) — this step preserves what
was already referenced, it does not scan folders for what wasn't.

Write a newly-introduced `![[target|alias]]` embed's target as whatever
path is available — a bare filename is fine. The engine itself (not this
skill) normalizes any embed target this revision newly introduces to the
destination entity's own sibling attachment folder, vault-root-absolute,
before writing (issue #76); a pre-existing embed already on the page is
left exactly as it was. This does not copy the referenced file anywhere —
only the reference's path is corrected. Until a file is actually placed at
the normalized path, the link still resolves to nothing on disk; that gap
is tracked separately, not by this skill.

## The three-tier dedup heuristic

Before merging a new fact into an existing note (especially a
tracker-style note that accumulates dated entries — updates, figures,
events), classify it against what's already recorded:

1. **Exact match** (same subject, same date, same value) → it's a
   duplicate. Skip it; don't append a second entry for the same fact.
2. **Fuzzy match** (same subject, same date, a similar-but-not-identical
   value) → flag for review rather than silently picking one. This is
   often a correction or a rounding difference, not a new fact — don't
   guess which.
3. **Conflicting value** (same subject, same date, a materially different
   value) → add it with an explicit note that it conflicts with the
   existing entry. Never silently overwrite the earlier figure and never
   silently drop the new one.

Only tier 1 is safe to resolve automatically. Tiers 2 and 3 are exactly the
kind of ambiguity that "Preview before writing" (below) exists to surface —
report the conflict, cite both entries, and let the confirmation step
decide.

## Resolving conflicting sources

When merging facts that come from more than one source, and they disagree,
apply source precedence rather than picking arbitrarily — highest authority
first: (1) the user's direct statements, (2) the note's existing
State (its current synthesized understanding), (3) Timeline entries (raw
evidence already on the page), (4) external sources (API enrichment, web
search). When two sources genuinely conflict, note the contradiction with
both citations in the Timeline — never silently resolve it by picking one.

Attribute each claim to whoever actually said or clearly implied it, not
whoever it's about. When a founder describes their own company's numbers,
the holder of that claim is the founder, not the company — companies don't
speak, their employees do. A self-reported figure is written as
self-reported, not elevated to verified fact just because it's now the
newest entry on the page.

## Extraction integrity

Never trust working memory for a figure, amount, date, or quote you're
about to write onto a page. If the fact came from a saved raw source
(an ingested transcript, article, or captured email), re-read it from that
file at the moment of writing rather than recalling it from earlier in the
conversation — long-context recall degrades and transposes digits in ways
that silently corrupt exactly the kind of data this skill exists to merge
carefully.

## Preview before writing

Findings and proposed merges are informational until confirmed — this skill
never auto-applies a revision, the same way the rest of wakil's write path
never does. `ingest_service.py`'s enrichment DAG and
`schema_migrate_service.py`'s migration planner both split into a
plan/propose phase that touches nothing and a separate apply phase that
only runs after confirmation (`schema_migrate_service` even re-reads each
file immediately before writing and skips any file that changed since
planning, rather than overwriting it blind). Follow the same shape here:
produce the diff, show what's additive vs. what's flagged as a conflict,
and only write once that's confirmed.

## Provenance and timestamps

- Update the `updated:` frontmatter field (present on identity-type schemas
  such as `person`/`company`, alongside `created:`) when a revision lands —
  don't leave it stale after a real content change.
- Never invent or leave a placeholder date. If the new fact's date isn't
  known, say so explicitly rather than writing "recently" or today's date
  as a guess.
- Never replace a developed note with a freshly generated summary, even
  when the summary is accurate — a shorter, cleaner-looking page that drops
  prior detail is the clobbering bug wearing a different outfit.

## Don't fill a gap with invented detail

Naming a real gap is useful; making it concrete with specifics you didn't
read is fabrication that reads as corroboration. A transcript with a genuine
diarization failure produced prose asserting a tool's internal behaviour,
verbatim noise tokens, and a call duration wrong by four minutes — none of it
in the input.

State the gap in the terms the source supports and stop. No illustrative
examples, sample quotes, or numbers you didn't read there. Precision is taken
as evidence.

## Scope absence claims to what you actually read

"Never mentions X" requires having seen the whole source, and you often
haven't — long sources are truncated before they reach you, and the text says
so when it happens. This is not a ban on recording that something is unknown:
where a rule above requires that (an unguessable date, an item the source
didn't settle), phrase it as a limit on your reading — "not stated in the
portion provided" — never as a property of the source.

## Pronouns: don't infer them

**A gendered pronoun is a factual claim about a person and needs a source,
exactly like a date does.** Only two things are that source: the person's own
`pronouns:`/`gender:` field (the `person` schema carries one), or an explicit
statement in the source you're merging — the person stating their own, or a
participant stating them by name. When a source supplies them and the page has
none, **write the `pronouns:` field** as part of this revision, cited like any
other fact; nothing else populates it.

A first name, a role, a voice, what reads naturally, and third parties'
incidental usage are not sources. Absent one you don't know, so write as if
you don't — they/them, or restructure around the name. Gendered pronouns
arrive by reflex mid-sentence rather than by decision, which is why Step 5 is
an explicit scan.

Once the merge is written and confirmed, hand off to `note-conformance` for
the shape/schema pass, then `kb-commit` to land it.
