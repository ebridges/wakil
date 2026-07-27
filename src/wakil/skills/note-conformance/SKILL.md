---
name: note-conformance
description: Audit a note's frontmatter, slug, links, and prose against wakil's schema and output-quality bar, and fix what's mechanical or flag what isn't. Use after drafting or revising a note, or when auditing existing pages for schema/prose drift.
skill_api: 1
---

# note-conformance

This is a checking skill, not a content-generating one. `content-synthesis` and
`note-revision` decide what a note says; `note-conformance` is the pass that
verifies the result is shaped correctly before it goes through `kb-commit` —
schema-valid frontmatter, a consistent slug, deterministic links, and prose
that meets the no-slop bar. Run it on one note at a time; it produces a short
list of fixes applied and issues flagged, not a rewrite.

That boundary holds even when directly asked to also update a note's
content while running conformance on it. Decline that part explicitly, name
`note-revision` as the skill that owns it, and do only the shape/schema
audit — a direct instruction to merge in a new fact doesn't widen this
skill's scope, it's still a handoff.

## When to use

- After drafting a new note or substantially revising an existing one.
- Before `kb-commit`, as a last check on anything about to be written to the
  knowledge base.
- When explicitly asked to audit existing pages for drift (stale frontmatter
  casing, mismatched slugs, missing citations).

For frontmatter drift across many notes at once, prefer `wakil schema migrate`
(below) over hand-editing every file — let the deterministic tool do the
mechanical part, and reserve this skill's judgment for what it can't cover.

## Procedure

- [ ] Step 1: **Run the mechanical pass, then the real schema check.** For
      the note's entity type, run `wakil schema migrate --dry-run --type
      <type>` and read the diff; apply it (`--yes` or per-file confirm)
      before doing anything by hand — see "Mechanical fixes" below for
      exactly what this does and doesn't cover. Then, after any manual write
      via `entity-enrichment`, `note-routing`, or `content-synthesis`, run
      `wakil schema validate <file>` — this is the real `validate_frontmatter`
      conformance check (required fields, enum values), not another
      mechanical rename; a skill-driven write has no other Python-level
      enforcement of it. Run both — they check different things, and neither
      substitutes for the other.
- [ ] Step 2: **Check the category rule.** Confirm `name:` vs `title:`
      matches the entity's schema category — see "Category rule" below. This
      is a hard schema error, not a style preference.
- [ ] Step 3: **Check the page shape matches its category.** An identity-type
      note should read as Compiled Truth + Timeline; a document-type note
      should read as a synthesis of what happened, not a running log — see
      "Page shape by category" below.
- [ ] Step 4: **Check the three-part slug.** Filename, H1, and every
      wikilink pointing at this note must agree — see "Slug consistency"
      below.
- [ ] Step 5: **Check links.** Internal cross-references are `[[path]]`
      wikilinks using the note's full workspace-relative path; external
      links are copied verbatim from their source, never reconstructed. See
      "Links" below.
- [ ] Step 6: **Check prose against the no-slop bar**, including citations.
      No filler, no hedged citations, no LLM preamble, no placeholder dates,
      exact phrasing preserved where the phrasing is the insight, titles
      under 60 characters and specific, every non-obvious claim traceable to
      its source. See "No-slop output bar" and "Citations" below.
- [ ] Step 7: **Report, don't silently resolve, what you can't fix.** A fact
      with no traceable source, an entity type unknown to the schema, an
      ambiguous frontmatter conflict — flag these explicitly rather than
      guessing or deleting. See "Deterministic links, uncitable facts"
      below.

## Mechanical fixes: `wakil schema migrate`

`wakil schema migrate` (`src/wakil/app/schema_migrate_service.py`) is the
cheap tier only: mechanical fixes a linter could make, not judgment calls.
Concretely, per file, it:

- renames casing/naming duplicates (`end_date`→`end-date`, `start_date`→
  `start-date`, `linkedin-link`→`linkedin`, `authors`→`author`, `link`→`url`),
  dropping the old key only when it's an exact duplicate value — a genuine
  conflict (both present, different values) is left in place and surfaced,
  never silently resolved;
- retypes `organization/`-directory files that were declared `type: concept`
  back to `type: organization`;
- aligns a mechanically re-cased `title` back to an authored-case `name` when
  they differ only by casing (guards a title-caser artifact like `1NSP` →
  `1nsp` without touching a genuinely different title);
- normalizes a quoted `type: "..."` value.

It does **not** touch page structure, section shape, exact-phrasing
preservation, or title quality — those stay this skill's prose judgment (see
"No-slop output bar"). It also never retypes an unknown `type` or resolves an
ambiguous rename; those are always left for manual review.

Run it with `--dry-run` first and read the diff before applying — this is
`wakil`'s own implementation of test-the-sample-before-the-batch discipline:
`plan_schema_migration` only proposes (nothing is touched during planning),
and `apply_migrations` re-reads each file immediately before writing,
skipping any file that changed on disk since the plan was made rather than
overwriting it blind. Use `--type <type>` to scope a run to one entity type,
`--yes` to apply without per-file confirmation once you've reviewed the plan,
and `--commit` to have each type's fixes land as its own `wakil chore: ...`
commit.

`wakil schema validate <path>...` (`src/wakil/app/schema_validate_service.py`)
is the complementary, non-mechanical check: it parses each file's
frontmatter and runs it through the same `validate_frontmatter` gate
`wakil enrich` already applies before every write, reporting required-field
and enum-value errors and exiting non-zero if any file fails. It renames or
rewrites nothing — run it after `wakil schema migrate`, not instead of it.

## Category rule: `name:` vs `title:`

The identity/document split is enforced in code
(`src/wakil/schema/validate.py:62-69`), not by convention — get it wrong and
`validate_frontmatter` returns a hard `SchemaError`, not a warning:

- **identity** types (e.g. `person`, `company`) use `name:` only. A `title:`
  field on an identity type is an error.
- **document** types (e.g. `meeting`) use `title:` only. A `name:` field on a
  document type is an error.

```yaml
# identity type (src/wakil/schema/entities/person.yaml) — name only
---
type: person
name: Jane Doe
status: active
created: 2026-07-16
updated: 2026-07-16
---
```

```yaml
# document type (src/wakil/schema/entities/meeting.yaml) — title only
---
type: meeting
title: Q3 planning sync
date: 2026-07-16
created: 2026-07-16
---
```

An `entity_type` the schema doesn't recognize is itself a hard error — don't
best-guess a schema for an unfamiliar `type:`; flag it.

## Page shape by `page_shape`, not by category

Every entity type declares its own `page_shape:` in its schema file
(`src/wakil/schema/entities/*.yaml`) — a separate axis from `category`.
Using the wrong shape is a conformance failure even when frontmatter
validates cleanly, and `category` is the wrong signal to infer it from:
`category` drives the `name:`/`title:` rule (does this type accumulate an
*identity*), while `page_shape` drives narrative structure (does this type
describe one occurrence or an ongoing subject) — they usually agree but not
always. `organization` and `project` are both `hybrid` category, but
`organization` uses the `single-occurrence.md` shape and `project` uses
`compiled-truth-timeline.md`; check the schema's own `page_shape:` field,
don't re-derive it from category.

The two shape templates currently defined (`src/wakil/schema/templates/`):

- **`compiled-truth-timeline.md`** — for a subject that accumulates
  knowledge across many separate sources over time (`person`, `company`,
  `project`, `concept`, `journal`, `assessment`...). Compiled Truth on top
  (synthesized, always-current — `entity-enrichment`'s "texture over facts"
  belongs here), an append-only Timeline/Log at the bottom
  (`note-revision`'s Timeline discipline governs how it grows). Never
  regenerate the top section from only the newest source — see
  `note-revision`'s "clobbering bug."
- **`single-occurrence.md`** — for a note describing one dated event or
  standalone artifact, not an accumulating subject (`meeting`, `reflection`,
  `idea`, `organization`, `meta`, `source`...). No Timeline — a log of one
  occurrence is just the occurrence restated. Summary / Key Decisions /
  Action Items / Discussion Notes / Open Questions, but only the sections
  that actually apply to the type in front of you (a meeting almost always
  has action items; a reflection almost never does — see the template's own
  guidance on when to omit a section versus state "None").

`resolve_page_shape_template()` (`src/wakil/schema/loader.py`) resolves each
shape's template the same kb-local/user/built-in way entity schemas resolve
— but as its *own* override unit (`schema/templates/<shape>.md`, not
`schema/entities/<type>.yaml`), so a workspace can restyle a shape's prose
without forking any type's field list, or repoint a type at a different
shape without touching that shape's template. Both are read directly by
`wakil enrich`'s extraction step now (`build_extraction_prompt` renders the
full catalog), not left to survive `SCHEMA.md` prose truncation.

## Slug consistency

A note's slug appears in three places, and all three must be the same
lowercase-kebab string:

1. the filename (minus `.md`);
2. the H1 heading at the top of the note body;
3. every `[[wikilink]]` elsewhere in the knowledge base that points at this
   note.

`slugify()` (`src/wakil/app/ingest_service.py:661`) is the reference
implementation wakil itself uses (and `git_service.py` reuses for branch
names): lowercase, non-alphanumeric runs collapsed to a single `-`, leading
and trailing `-` stripped. Use it as the definition of "correctly slugged,"
not a separate hand-rolled rule. A note whose H1 is Title Case, or whose
filename and H1 disagree, is a conformance failure — fix the drifting one to
match the filename, don't rename the file to match a stray heading.

## Links

**Internal cross-references** use the wikilink form `[[path]]`, where `path`
is the note's full workspace-relative path — never a bare filename, never a
title string, and never a path built relative to the linking file (no
`../...`). `schema/validate.py`'s `ref` field kind only checks that a
reference is a string; resolving it to an actual note is the knowledge
base's own `RESOLVER.md` (a workspace-provided file wakil reads, not one it
ships) — defer to whatever path/slug convention the open workspace's
`RESOLVER.md` states, but the "absolute path from the vault root, never
relative" rule itself is non-negotiable regardless of workspace.

**External links** are copied verbatim from their source — see "Deterministic
links" below.

**Embeds** use `![[target|alias]]` — target first, alias second, the same
order as a plain wikilink. The target follows the same path rule as
`[[wikilinks]]` in Links above: an absolute path from the vault root, never
a bare filename and never a path resolved relative to the note's own
directory. `![[attachments/Sapwood/diagram.png|Sapwood diagram]]` embeds
that file and displays it captioned "Sapwood diagram." The reversed order,
`![[Sapwood diagram|attachments/Sapwood/diagram.png]]`, is wrong: it embeds
a file literally named `Sapwood diagram` and captions it with the path — a
broken embed, not a captioned one. A bare-filename target such as
`![[diagram.png|Sapwood diagram]]` is a conformance failure even when the
argument order is right, the same as an unresolvable relative wikilink. The
alias is always optional and always second.

## No-slop output bar

Notes are durable artifacts, not chat output:

- No filler phrases ("It's worth noting that...", "Interestingly...").
- No hedging when a fact is cited — "X is true" per the source, not "X might
  be true."
- No LLM preamble ("I've created...", "Here's the updated...", "Certainly!").
- No placeholder dates ("recently," an unfilled `YYYY-MM-DD`).
- Preserve a speaker's exact words rather than paraphrasing when their
  phrasing is itself the insight — don't clean up grammar or swap in your
  own terminology for a person's own framework or coinage.
- Titles are descriptive enough to find in a search result, under 60
  characters, not full sentences, and not generic ("Jane Doe, VP Eng at
  Acme" not "Person Page").

## Citations

Every non-obvious claim in a note — a figure, a quote, a stated intention,
anything that isn't common knowledge — should carry inline provenance so a
reader can tell what's grounded versus inferred. Wakil's default annotation:
a parenthetical tag naming the kind of provenance — `(observed)`,
`(self-reported)`, `(inferred)`, `(reported: <source>)` — placed at the point
of the claim, not batched into a footnote. Use the workspace's own citation
format instead when its `SCHEMA.md` defines one; the point is that *some*
consistent, checkable annotation exists, not the exact bracket shape.

A note with zero inline provenance markers anywhere is itself a conformance
smell worth flagging, the same way a missing citation on one specific claim
is — durable notes get reread and acted on long after the source context is
gone, so "what does this rest on" needs to survive that gap.

## Deterministic links, uncitable facts

Never guess or reconstruct a URL — a wikilink is built from the note's actual
path, an external link is copied from the source that supplied it, never
composed from a slug or a memory of what the URL "should" be.

When a fact has no traceable source, or a link can't be built deterministically,
**flag it rather than inventing a citation or silently deleting the fact.**
Both failure modes are worse than an honest gap: a fabricated source misleads
every future reader, and a deleted fact is knowledge the base quietly loses.
