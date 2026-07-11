---
title: Entity Metadata — A Critical Read of schema.md §4
status: draft
audience: wakil design
---

# Entity Metadata

`docs/entity-resolution.md` covered **where** knowledge lands (`RESOLVER.md`).
`docs/entity-model.md` covered the **Compiled Truth / Timeline** shape of an
important page. This doc covers what's left: **`schema.md` §4, "Entity
Frontmatter Schemas"** — the YAML contract each entity type is supposed to
carry — audited against how ~2,500 real files in the target vault actually
use it. Where the earlier docs sampled a handful of files per finding, this
one censuses whole categories (every `person` file, every `company` file,
etc.) so the claims below are counts, not impressions.

## The vault doesn't have 9 types — it has ~30

`schema.md` §4 documents nine: `person`, `company`, `project`, `meeting`,
`concept`, `source`, `journal`, `assessment`, `reflection`. A vault-wide
census of `type:` frontmatter values turns up considerably more:

| Type | Count | Documented? |
|---|---|---|
| `journal` | 939 | yes |
| `person` | 430 | yes |
| `concept` | 392 | yes (282 physically in `concepts/` — see below) |
| `company` | 260 | yes |
| `project` | 224 | yes |
| **`learning-agenda`** | **151** | **no** |
| `source` (+ `"source"` quoted) | 127 + 29 | yes |
| `meeting` | 63 | yes |
| `assessment` | 28 | yes |
| `reflection` | 26 | yes |
| `idea` | 12 | no (routing rule exists in `RESOLVER.md`, no schema) |
| `meta` | 8 | no |
| `report`, `article` | 5, 4 | no |
| `documentation` | 3 | no |
| `transcript`, `research`, `procedure` | 2 each | no |
| `organization`, `index`, `decision`, `daily-note`, `convention`, `workstream`, `working-session`, `project-draft`, `original`, `observation`, `encounter` | 1 each | `organization` has a routing rule, no schema; rest undocumented |

Two of these are not noise. `learning-agenda` (151 files, structured
day-by-day curricula under `learning-agendas/<curriculum>/day-NN-<topic>/`)
is bigger than `meeting`, `assessment`, and `reflection` combined, and
`schema.md` and `RESOLVER.md` are both silent on it. `organization` — the
type `RESOLVER.md` rule 8 routes to — is used as a literal `type:` value
exactly once; the other ~104 files in `organization/` declare `type:
concept` instead (see below). The `concept` row's discrepancy (392 vault-wide
vs. 282 in `concepts/`) is explained almost entirely by this: `organization/`
borrowing `concept`'s schema wholesale is not a one-off, it's most of the
gap.

## Per-type audit: documented schema vs. real usage

For each documented type, the table below gives the schema's declared fields
against the field actually observed in every file of that type, with the
count and percentage where it's informative.

### `person` (`people/*.md`, 430 files)

Core fields (`name`, `type`, `company`, `status`, `tags`, `role`,
`relationship`, `linkedin`, `aliases`, `email`, `github`) sit at 86–99%
adherence — the best-conforming identity type in the vault. Undocumented
fields in real use: `end-date` (109 files, 25%), `linkedin-link` (14, a
duplicate of `linkedin`), `team` (10), `link` (8), `level` (8), `last_name`/
`first_name` (8 each, competing with `name`), `title` (7 — a direct
violation of §4's own rule, see below), `linear_id` (7, a Linear
integration), `confidence` (7, correctly used per §3), `start_date`/
`location`/`_migrated_sources` (≤6 each).

### `company` (`companies/*.md`, 256 files)

Also strong: `category` (251, 98%), `aliases` (225, 88%), `website`/`stage`
(219 each), `key-people`/`industry` (218 each), `interview-dates` (215, 84%).
The interesting drift here is contamination, not extension: 4 files each
carry `role`, `relationship`, `linkedin`, `github`, `email` — person
schema fields on a company page, almost certainly a migration script
applying the wrong template to a handful of files.

### `project` (`projects/**/*.md`, 220 files)

**Every single project file (220/220) carries `title:`, and 219 also carry
`name:`.** §4 states explicitly that entity types "do NOT add a `title:` key
to an entity page." For `project`, that rule is violated at 100% — not a
handful of stragglers, the entire category. Also present and undocumented:
`year`/`week`/`day`/`date` (37 files — date-anchored, journal-shaped
sub-entries), `pillar` (17), and a `product_lead`/`layer`/`engineering`/
`design`/`customer_sponsors` cluster (9 files each — reads like one
specific team's RACI convention, not a vault-wide one).

### `concept` (`concepts/*.md`, 282 files)

Same story as `project`: `title:` appears on 277/282 files (98%), against
280 `name:` — the rule is violated almost universally here too. Also:
`status`/`end-date`/`company` (17 files each) and `workstream` (14) —
project-shaped fields on concept pages, which is either schema bleed or (per
`docs/entity-resolution.md`'s concept/project "hard call") content that was
never run through that disambiguation and is sitting in the wrong place.
`kindle-sync` (8 files) is a small, real Readwise/Kindle integration field.

### `meeting` (`meetings/*.md`, 62 files)

The highest-adherence type: `title`/`date`/`attendees` at 100%, `tags`/
`company` at 98%, `transcript`/`source`/`project`/`decisions`/`action-items`
at 94%. But `created` (50, 81%) and `updated` (21, 34%) are used constantly
despite not being in §4's `meeting` block at all, and — more importantly —
20 files carry `stage` and 18 carry `status`. This is very likely already an
informal implementation of the exact "when does a `sources/` transcript
count as synthesized" threshold `docs/entity-resolution.md` finding #9
flagged as undefined. It should be formalized, not invented from scratch.

### `journal` (`journal/**/*.md`, 939 files, 300 sampled)

Fully consistent across the sample: `year`, `week`, `type`, `topics`,
`title`, `tags`, `people`, `day`, `date`, `created` all at 300/300 — despite
`year`, `day`, `title`, `created`, and `topics` not appearing in §4's
`journal` block at all. This is the inverse of the drift seen elsewhere: real
practice is *more* complete and *more* consistent than the documentation.
`workstream`/`status`/`company` (43 files, 14%) mark entries that are
entirely about one project — a legitimate, minority-case extension.

### `source` (`sources/**/*.md`, 135 files)

The most field-diverse type in the vault by a wide margin — over 20 distinct
field names in use against a 5-field documented schema. Beyond the
documented `title`/`origin`/`url`/`captured`/`tags`: `created` (117, distinct
from `captured`), `published` (67, a *third* date — when the source itself
was published, vs. when it was captured), `source` (44 — a field literally
named `source` inside a `type: source` file), `company` (40), `description`
(37), `authors`/`author` (35/34, plural and singular coexisting), `link` (33,
duplicating `url`), `recording_url` (28), `readwise_updated`/`readwise_id`
(26 each, a Readwise integration). This isn't disorganized so much as
under-modeled: raw material genuinely has different natural metadata
depending on where it came from (a book highlight, a recorded meeting, a
tweet), and §4 already has exactly one precedent for handling that —
the optional `sources/messages/**` email-thread block — it just wasn't
extended to the other origins that clearly need it.

### `assessment` (`sensitive/**/*.md`, 27 files)

Perfect adherence on the documented fields (27/27 across `subject`,
`period`, `sensitive`, `tags`, `created`). But `company`, `workstream`, and
`status` each appear on 26/27 files (96%) and `end-date` on 25/27 (93%) —
near-universal, real, and entirely undocumented. This is the cleanest case
in the whole audit of "the schema should simply be updated to match
reality" — there's no ambiguity or contamination here, just a documentation
gap.

### `reflection` (`wiki/personal/reflections/*.md`, 24 files)

Already well-matched: 100% on `type`/`title`/`tags`/`created`, 96% on
`week`/`date` (23/24 — the missing one is `index.md`, `type: index`, not a
real reflection), 83% on `related`. No changes needed here — call this one
validated, not drifted. (Separately, `docs/entity-resolution.md`'s
recommendation to relocate this category under `ideas/reflections/` is a
routing question, not a schema one, and stands independently of this.)

### `idea` (`ideas/*.md`, 12 files) — no documented schema

Real files consistently carry `type`, `name`, `title`, `status: draft`,
`tags`, `created`, `updated`, `_migrated_from`. `docs/entity-resolution.md`
finding #1 already flagged the missing schema and finding #3 flagged the
lack of a promotion path (every file is stuck at `status: draft`). Both are
fixable in the same schema addition — see below.

### `organization` (`organization/**/*.md`, ~105 files) — no real schema

Confirmed at vault scale what earlier sampling suggested: real files declare
`type: concept` (the overwhelming majority), `type: organization` (2 files),
or even `type: person`/`source`/`procedure` (a handful) — `organization/` is
a directory, not a type. Field usage otherwise mirrors `concept` almost
exactly (`title`/`name`/`aliases`/`maturity`/`domain`/`related` all present
at similar rates), plus the same `status`/`end-date`/`company`/`workstream`
bleed seen in `concepts/` proper.

### `learning-agenda` (`learning-agendas/**/*.md`, 151 files) — not a new
epistemic category

`learning-agendas/` holds two curricula: `ai-engineering-topic-review` (11
clean `day-NN-topic/` folders) and a fully migrated Coursera "Machine
Learning" course (module-numbered files plus an `assignments/` subfolder of
actual code). Every leaf file's frontmatter (`name`, `title`, `aliases`,
`domain`, `maturity`, `related`, `tags`) is `concept`'s schema verbatim —
because a distilled course note *is* a concept by every test already in
`docs/entity-resolution.md` (external material, no owner, no due date,
reusable regardless of which course taught it). What's different isn't the
knowledge, it's that it's organized under a **curriculum container** rather
than sitting flat. That container is real, not hypothetical:
`machine-learning/00-readme.md` exists, and carries a `course:
coursera-machine-learning` field distinct from `domain: machine-learning` —
a field no leaf file has, and the `ai-engineering-topic-review` curriculum
doesn't have an equivalent container file at all, so even the one place this
pattern exists, it's inconsistent. The container itself is better described
by the **project** test (owned, scoped, has a completion state — "am I done
with this course?") than by the concept test. So `learning-agenda` isn't a
third leaf next to consumed/produced; it's the existing concept/project
split applied recursively — see cross-cutting findings and the revised
schema below.

## Cross-cutting findings

**The "no `title:` on entities" rule isn't wrong, it's misclassified.**
`person` violates it in 7/430 files (1.6% — noise); `company` violates it in
0/256 (fully compliant). `project` violates it in 220/220 (100%); `concept`
in 277/282 (98%). The first instinct — treat `project`/`concept` as
exceptions to "entities use name, documents use title" — doesn't survive
checking whether `title:` is even real content. Sampled side by side,
`name`/`title` are usually the same string mechanically re-cased
(`concepts/subsets.md`: `name: Subsets` / `title: 'Subsets'`), and the
mechanism shows through where it breaks: `projects/1nsp/...md` has `name:
"1NSP Second Floor Amenity Renovation"` next to `title: "1nsp Second Floor
Amenity Renovation"` — a naive title-caser that doesn't know "1NSP" is an
acronym. That could read as "just delete the redundant field" — except
`title:` is confirmed *live* practice, not migration debris (checked: all 7
non-migrated `project` files, created after the migration with no
`_migrated_from`, still carry it), and it's exactly the field that carries
real, non-mechanical authored content when someone bothers to write one —
`learning-agendas/machine-learning/00-readme.md` has `name: README` next to
`title: 'Chapter 0: Readme'`, information no case-transform of `name` could
produce.

That reframes the fix. The rule's *mechanism* is right — a stable,
slug-derived `name` for identity and linking, a freely-editable `title` for
display — the bug is that it only recognizes two categories when there are
three:

| Category | Types | Convention | Why |
|---|---|---|---|
| Identity | `person`, `company` | `name` only | The name *is* the display label; a separate title only restates it |
| Document | `meeting`, `source`, `reflection`, `journal` | `title` only, no `name` | No proper-noun identity to protect — the filename is already date/content-derived |
| Hybrid | `concept`, `project`, `organization`, `idea`, `learning-agenda` | both | Needs a stable slug for wikilink integrity **and** benefits from a headline that can be rewritten without breaking every inbound link — `name` and `title` are allowed to start identical and diverge |

Every sampled type lines up with one row: `meeting`/`source`/`reflection`/
`journal` frontmatter never carries a `name:` field at all in the census
above, which independently confirms the "document" row rather than just
asserting it.

**Cross-type field bleed is real but small.** Person fields on 4 company
files, project-shaped fields (`status`/`end-date`/`company`/`workstream`) on
14–17 concept files and 26–35 assessment/source files. Most of this reads as
migration-script residue (wrong template applied) rather than a deliberate
convention, except the assessment case, which is consistent enough (93–96%)
to just be the real schema.

**Documented-but-unused conventions mirror `entity-model.md`'s Compiled
Truth finding.** §3's `confidence: low|medium|high` field appears in 26
files out of ~2,500 (~1%). Its companion convention — inline provenance tags
like `(observed)`, `(inferred)`, `(reported: ...)` — appears in 8 files
total. Both are well-specified and almost never used, the same "policy on
paper, absent in practice" shape `entity-model.md` already found for
Compiled Truth being empty on most pages. A convention that requires
remembering to hand-annotate uncertain facts during normal writing doesn't
survive contact with actual note-taking; it needs to be prompted for at a
specific moment (synthesis time) or it won't happen.

**Tagging has genuinely improved since the pre-migration vault.**
`planning/vault-refactoring-recommendations.md` (the pre-migration analysis,
dated 2026-02-16) found only 7% of the old vault's files had a `tags:`
field at all. A 1,000-file sample of the current vault shows 978 files
(98%) carry the field, with 107 of those (11%) empty. Worth crediting
explicitly — the migration fixed the biggest problem that earlier audit
flagged — while noting the remaining 11% empty-tags gap as the next
increment, not a new problem.

**Casing and naming duplicates are small but should be cleaned up
mechanically**: `end-date` / `end_date` / `start_date` (dash vs.
underscore), `linkedin` / `linkedin-link`, `author` / `authors`, `link` /
`url`, `type: source` / `type: "source"` (unquoted vs. quoted — same value,
inconsistent style). None of these are ambiguous to a reader; they're the
kind of thing a linter catches in one pass rather than something that needs
new policy.

## Proposed conventions per entity type

Changes are additions or corrections grounded in the counts above, not new
invention. Fields marked **NEW** aren't new to the vault — they're already
in real use and being added to the documentation to match.

```yaml
# person — people/first-last.md (unchanged except one addition)
type: person
name: First Last
aliases: []
company: <company-slug>
role: ""
status: active | former | candidate | prospect | contact
relationship: coworker | report | manager | candidate | recruiter | vendor | mentor
linkedin: ""
github: ""
email: ""
end-date: ""            # NEW — when status left "active"; already in 25% of files
tags: []
created: <date>
updated: <date>
# confidence: low|medium|high   — already documented in §3; keep optional, don't require
```

```yaml
# company — companies/<slug>.md (unchanged)
type: company
name: ""
aliases: []
category: employer | vendor | interview-target | prospect | partner | consulting-client
status: current | former | active | inactive
website: ""
industry: ""
stage: ""
interview-dates: []
key-people: []
tags: []
created: <date>
updated: <date>
```

```yaml
# project — projects/<company|personal|misc>/<slug>.md
type: project
name: ""
title: ""                # CORRECTED — permit alongside name; 100% of real files already do this
company: <company-slug> | personal
status: active | paused | completed | archived
owner: ""
stakeholders: []
workstream: ""
start-date: <date>
end-date: ""
tags: []
created: <date>
updated: <date>
# team-specific role fields (product_lead, layer, engineering, design,
# customer_sponsors) are a real but narrow (9-file) local convention —
# tolerated as a free extension, not promoted to the vault-wide schema
```

```yaml
# concept — concepts/<slug>.md
type: concept
name: ""
title: ""                # CORRECTED — permit alongside name; 98% of real files already do this
aliases: []
domain: ""
maturity: seed | developing | stable
related: []
tags: []
created: <date>
updated: <date>
```

```yaml
# meeting — meetings/<date>-topic.md
type: meeting
title: ""
date: <date>
source: transcript | notes | slack | manual
status: raw | synthesized   # NEW — formalizes entity-resolution.md finding #9; already informal in 18 files
attendees: []
company: <company-slug>
project: <project-slug>
decisions: []
action-items: []
transcript: ""
tags: []
created: <date>          # NEW — already in 81% of files
updated: <date>          # NEW — already in 34% of files
```

```yaml
# journal — journal/YYYY/... (documentation catching up to practice)
type: journal
date: <date>
week: YYYY-Www
day: mon | tue | ...      # NEW — already in 100% of sampled files
year: <year>              # NEW — already in 100%
title: ""                 # NEW — already in 100%
topics: []                # NEW — already in 100%
people: []
tags: []
created: <date>           # NEW — already in 100%
```

```yaml
# assessment — sensitive/** (documentation catching up to practice)
type: assessment
subject: <first-last | self>
period: ""
sensitive: true
company: <company-slug>   # NEW — already in 96% of files
workstream: ""            # NEW — already in 96%
status: ""                # NEW — already in 96%
end-date: ""              # NEW — already in 93%
tags: []
created: <date>
updated: <date>
```

```yaml
# reflection — ideas/reflections/** (per docs/entity-resolution.md; unchanged shape)
type: reflection
title: ""
date: <date>
week: YYYY-Www
related: []
tags: []
created: <date>
```

```yaml
# idea — ideas/<slug>.md (NEW — no prior schema)
type: idea
name: ""
title: ""
status: draft | active | shelved | promoted
promoted_to: ""           # NEW — [[projects/.../slug]] once split into a project; closes entity-resolution.md finding #3
tags: []
created: <date>
updated: <date>
```

```yaml
# organization — organization/<slug>.md (NEW — was borrowing concept's schema)
type: organization
name: ""
title: ""
aliases: []
domain: ""                 # e.g. career-ladder | star-story | principles | interview-prep
company: <company-slug>    # NEW relative to concept's schema — which employer this describes, when applicable
maturity: seed | developing | stable
related: []
tags: []
created: <date>
updated: <date>
```

```yaml
# learning-agenda curriculum container — projects/personal/<curriculum-slug>.md (NEW)
# the course/curriculum as a whole: owned, scoped, has a completion state —
# the project test, not the concept test. Replaces the one-off `00-readme.md`
# pattern with the type that already models "owned + has an end-state".
type: project
name: ""
title: ""
company: personal
workstream: learning
status: active | completed | paused
start-date: <date>
end-date: ""
tags: []
created: <date>
updated: <date>
```

```yaml
# learning-agenda topic leaf — learning-agendas/<curriculum>/day-NN-<topic>/<topic>.md
# EXPLICITLY documented as concept's schema plus one field, not a parallel
# copy — this is what justifies the distinct type value at all.
type: concept
name: ""
title: ""
aliases: []
domain: ""                 # broad subject, e.g. machine-learning — NOT the curriculum identity
curriculum: ""              # NEW — [[projects/personal/<curriculum-slug>]], the container above
maturity: seed | developing | stable
related: []
tags: []
created: <date>
updated: <date>
```

```yaml
# meta — vault-infrastructure pages (schema.md, RESOLVER.md, planning docs) (NEW)
# collapses "meta" (8 real files) and "documentation" (3) into one value —
# they're the same thing under two names; "meta" is the more common choice
type: meta
title: ""
created: <date>
updated: <date>
```

```yaml
# index — MOC / navigation pages (NEW)
type: index
title: ""
tags: []
created: <date>
```

`source` is deliberately not given one more field-bloated block. Instead,
extend the existing `sources/messages/**` email-thread precedent with two or
three more origin-keyed sub-schemas (article/clipping, Readwise book
highlight, recording/transcript), each declaring only the fields real files
of that origin actually use, rather than growing one flat schema toward 20+
optional fields.

## Recommendations

1. **Document the four types with the largest gap between real usage and
   written schema first**: `assessment` (93–96% real usage of 4 undocumented
   fields, zero ambiguity), `meeting` (`status: raw|synthesized` directly
   closes an open question from `docs/entity-resolution.md`), `idea`, and
   `organization`. These are the highest-value, lowest-risk fixes — no
   migration, just writing down what's already true.
2. **Replace the two-way name/title rule with the three-way identity /
   document / hybrid split above**, rather than continuing to flag 100%/98%
   of `project`/`concept` files as non-compliant with a rule that
   misclassifies them.
3. **Retype `learning-agenda` leaves to `concept` + `curriculum:`, and give
   each curriculum a `project` container** — cheap tier: document the
   mapping and use it for anything created going forward. Expensive tier,
   flagged not executed: retyping the 151 existing leaf files and adding
   container files for both existing curricula is a real migration, same
   shape as `docs/entity-resolution.md`'s reflections move — sequence it
   after the zero-cost documentation fixes above.
4. **Split `source`'s schema by origin**, extending the one precedent that
   already exists (email-thread), instead of leaving one flat 5-field schema
   against 20+ real fields.
5. **Don't chase every low-n field into the schema.** The 9-file RACI
   cluster on `project`, the 8-file `kindle-sync` field — real, but narrow
   enough to leave as tolerated free-form extensions rather than vault-wide
   convention, per this project's own "does this clearly improve" bar.
6. **Treat `confidence:` and inline provenance tags as a prompted step, not
   a writing-time convention.** At ~1% and ~0.3% real usage respectively
   despite being fully specified, the fix isn't clearer documentation — it's
   moving the ask to a moment where it's cheap to answer (see below).

## Implications for `wakil`

`wakil` already stores frontmatter as an opaque blob
(`Note.frontmatter_json`, `src/wakil/storage/schema.py:70`), parsed via
`python-frontmatter` in `src/wakil/knowledge/markdown.py`. That's the right
call given how wide the real drift documented above is — a strict per-type
Pydantic model would reject a large fraction of this vault's actual files.
Two concrete, checked-not-assumed follow-ups:

- **`wakil` is already contributing to this drift.** `_build_raw_file` in
  `src/wakil/app/ingest_service.py` (~line 300) hardcodes
  `type: source`, `source_type:`, `origin:`, `title:`, `retrieved:` when
  writing a new raw capture. The target vault's own `source` schema uses
  `captured:`, not `retrieved:`, for the same concept, and has no
  `source_type:` field at all. `wakil` should read a target vault's
  `schema.md` (or a config derived from it) for field names rather than
  hardcoding one vault's conventions into `ingest_service.py` — the
  `retrieved`/`captured` mismatch is a direct, fixable instance of exactly
  the kind of drift this document catalogues, introduced by the tool meant
  to prevent it.
- **`confidence:` and provenance annotations belong in the promotion step,
  not the capture step.** `docs/entity-model.md`'s proposed `wakil entity
  compile <slug>` command (Compiled Truth synthesis) is the natural place to
  ask "confidence for this claim?" as a cheap inline prompt during the
  diff-confirm flow — matching where the two real users of `confidence:` in
  this vault actually set it (on synthesized pages, at write time) rather
  than expecting it to be added retroactively, which the ~1% usage rate
  shows doesn't happen.
