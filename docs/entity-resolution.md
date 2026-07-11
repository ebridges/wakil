---
title: Entity Resolution and Routing — A Critical Read of RESOLVER.md
status: draft
audience: wakil design
---

# Entity Resolution and Routing

`docs/entity-model.md` covers **shape**: what a Compiled Truth / Timeline page
looks like once it exists. This doc covers **place**: which of the ~20
top-level directories in the target vault a given piece of raw knowledge
should land in before it can be shaped at all. The target vault's own
`RESOLVER.md` is a worked example of this problem — a priority-ordered
decision list that claims to be "the single authority for *where* knowledge
lands." This is a critical read of that document against how the vault it
governs actually looks on disk, with an eye toward what `wakil`'s own
resolver logic (`ingest_service.py`'s "existing related-note search /
resolver routing," referenced but not yet specified in `entity-model.md`)
should and shouldn't copy.

## The mechanism, as written

`RESOLVER.md` is a ten-rule waterfall, evaluated top to bottom, first match
wins:

| # | Test | Destination |
|---|------|-------------|
| 1 | Sensitive (review, feedback, compensation) | `sensitive/` — **stop** |
| 2 | Person | `people/first-last.md` |
| 3 | Company / employer / vendor / client | `companies/<slug>.md` |
| 4 | Meeting / transcript / session note | `meetings/` (synthesized) or `sources/` (raw) |
| 5 | Project (owned, dated, deliverable) | `projects/<scope>/<slug>.md` |
| 6 | Concept (reusable, instructional, timeless) | `concepts/<slug>.md` |
| 7 | Idea (generative output, proposal, writing) | `ideas/<slug>.md` |
| 8 | Career / org structure (ladders, STAR stories) | `organization/<slug>.md` |
| 9 | Dated work log | `journal/YYYY/...` |
| 10 | Otherwise | `archive/` (historical) or `inbox/` (unresolved) |

Two things are layered on top of the waterfall: a dedicated **"hard call"**
section that disambiguates rule 5 vs. rule 6 with a worked test (owner/due
date vs. none) and a `split` verb for content that is genuinely both; and a
**linking rule** (absolute vault-root paths only, never relative) that keeps
cross-references stable when a file moves or is split. `schema.md` then picks
up where `RESOLVER.md` stops, defining the YAML frontmatter and page shape
for each destination.

## What works

- **Priority ordering resolves the one conflict it was built for.** A
  performance review *about* a person still hits rule 1 before rule 2, so
  sensitive content never has a chance to land in a personal file that gets
  casually surfaced. That's the right design for the one ambiguity the
  document was clearly written around.
- **The concept/project split is a real disambiguation, not just a
  definition.** It gives an owner/due-date test *and* an escape hatch
  (`split`) for mixed content, rather than forcing a false binary. This is
  the one pair the document treats with the rigor the whole taxonomy needs.
- **"Exactly one canonical file" plus absolute linking is a coherent pair.**
  Because links never encode a relative path, a file can be moved or split
  without breaking every page that references it — which matters a lot if
  `split` is a first-class operation.

## Where it breaks

The vault this document governs currently holds 427 `people/`, 256
`companies/`, 282 `concepts/`, 220 `projects/`, 105 `organization/`, 62
`meetings/`, 136 `sources/`, 27 `sensitive/`, 937 `journal/`, 12 `ideas/`, and
1 `archive/` file. That distribution, and the frontmatter actually in use,
surfaces problems the document's own text doesn't anticipate.

### 1. `idea` and `organization` are routing targets with no schema

`schema.md` §4 defines frontmatter for exactly nine types: `person`,
`company`, `project`, `meeting`, `concept`, `source`, `journal`,
`assessment`, `reflection`. `idea` and `organization` — rules 7 and 8 in
`RESOLVER.md`, first-class destinations — have no entry. In practice this
isn't a paper cut: every sampled file in `organization/` (e.g.
`ic5-staff-technical-program-manager.md`,
`story-of-a-time-where-team-worked-great-together.md`) carries `type:
concept` in its frontmatter, not `type: organization`. The category exists
only as a directory convention; the shaping layer can't tell an org-ladder
note from a concept note once you're inside the file. Rule 8 gets you a
folder, not an entity type.

### 2. The waterfall structurally pre-empts rule 8

This is worse than a documentation gap. Because the list is first-match-wins
and concept (rule 6) is checked *before* organization (rule 8), any
org-structure content that also happens to read as "reusable /
instructional" — which a STAR story or a leveling rubric usually does — gets
caught by rule 6 first. `story-of-a-time-where-team-worked-great-together.md`
is a case in point: nothing in rule 6's text excludes career-narrative
content, so the only thing routing it to `organization/` instead of
`concepts/` is a human who already knows the "real" answer and skips the
list. A priority-ordered decision list only works if lower-priority rules
are actually reachable; here, rule 8 is reachable only by the router's prior
knowledge of the intended answer, which defeats the point of having a
waterfall at all.

### 3. `ideas/` is a write-once bin, not a category with a lifecycle

All 12 files in `ideas/` carry `status: draft`; none show any onward
transition. Most were bulk `_migrated_from` legacy PARA folders
(`work/business ideas/`, `personal/writing/`, `job-search/`) during a single
migration pass rather than accumulated through ongoing use — the category
has grown by roughly zero organically-created files since. Compare 12 ideas
against 282 concepts and 220 projects: whatever ambiguous, generative content
the vault produces, it is overwhelmingly *not* landing in `ideas/` in
practice, regardless of what rule 7 says. The rule exists; the routing
behavior it describes largely doesn't happen.

### 4. The idea/concept boundary has no test, and the vault shows why that matters

Rule 6 gets a precise test ("no owner, no due date"). Rule 7 gets a category
description ("productive/generative output, proposals, writing") with no
comparative test against rule 6 and no `split` equivalent. The consequence is
visible in the 12 real files: `ideas/tweets.md`, `ideas/10y.md`, and
`ideas/blog-post-on-interviewing.md` are recognizably idea-shaped (personal,
generative, no reusability claim). But `ideas/prompt-health-data-world-model.md`,
`ideas/prompt-photo-uploader-app.md`, and `ideas/prompt-reflections-app.md`
read as scoped product specs — arguably a project once someone picks them up
(rule 5's test), or a reusable pattern if the "world model" idea generalizes
(rule 6's test). `ideas/` is quietly holding two different populations —
creative/personal seeds and proto-project specs — with nothing in the
resolver distinguishing them, because nothing asks the same owner/due-date
question rule 6 vs. 5 already answers.

### 5. The document claims total authority but routes to less than half the vault

`RESOLVER.md` calls itself "the single authority for *where* knowledge
lands." The vault root has roughly twenty non-dotfile directories; the
ten-rule waterfall accounts for ten of them. `drafts/`,
`dream-cycle-summaries/`, `interview-prep/`, `learning-agendas/`, `media/`,
`planning/`, `reports/`, `scratchpad_whisper/`, and `wiki/` all hold real
content and are never mentioned. One of them, `wiki/personal/reflections/`,
is even the destination named in `schema.md`'s own `reflection` type — the
shaping document and the routing document disagree about what's
authoritative. A router that's silent on half its own vault isn't wrong so
much as it's overclaiming: "single authority" should mean the decision list
is exhaustive, or the claim should be softened to "authority for entity-type
content," which is what it actually is.

### 6. `inbox/` is prescribed and doesn't exist

Rule 10's fallback for genuinely unresolved content is "`archive/`
(historical) or `inbox/` (unresolved — flag it)." There is no `inbox/`
directory anywhere in the vault. The one mechanism the resolver offers for
"I don't know, come back to this" has no physical home, which means in
practice rule 10 always resolves to `archive/` — collapsing a documented
two-way decision into a one-way default no one wrote down.

### 7. `archive/` is real but essentially unused, with an undocumented sub-namespace

`archive/` holds exactly one file, `archive/memory/feedback-daily-agenda-todoist.md`.
The `memory/` sub-namespace isn't described anywhere in `RESOLVER.md`, which
just says "historical." A single-file category is not itself a problem, but
combined with finding #6, it suggests `archive/` is being used as an ad hoc
escape valve rather than a deliberate destination — there's no stated policy
for *when* something crosses from "still relevant, leave it where it is" to
"historical, move it," so nothing does.

### 8. "Exactly one canonical file" collides with how source material actually arrives

`RESOLVER.md` states every piece of knowledge resolves to exactly one
canonical file. But the vault's own `meeting` schema (`schema.md` §4) has
`attendees`, `company`, and `project` fields — a single meeting transcript
routinely touches a person, a company, and a project simultaneously. In
practice this is resolved by extraction fan-out (one raw input updates N
target entity pages plus one meeting page) rather than single-file
resolution, but the resolver's stated rule doesn't describe that — it reads
as if raw input maps 1:1 to a destination, when the real behavior, and the
behavior `wakil`'s own ingest pipeline needs to replicate, is 1:N.

### 9. No stated synthesis threshold between `meetings/` and `sources/`

Rule 4 splits on "synthesized" vs. "raw," but nothing defines what
constitutes synthesis, who performs it, or when a `sources/` transcript
should be promoted into a `meetings/` note. This is the same raw-to-durable
promotion gap `entity-model.md` already flags for Compiled Truth — it isn't
unique to meetings, but the resolver is the layer that should say *when* a
file crosses that line, and currently doesn't.

## Reflection under a produced-knowledge category, and a consumed/produced
axis for finding #4

Two follow-up questions worth resolving here rather than leaving open: where
should a `reflection` route to, and does reframing `concepts` as *consumed*
knowledge and `ideas` as *produced* knowledge sharpen finding #4's
idea/concept ambiguity.

**Reflection's current destination is the problem, not just an undocumented
one.** `schema.md` fully specifies `type: reflection` →
`wiki/personal/reflections/**`, and sampled files confirm it's used exactly
as documented (`wiki/personal/reflections/2026-05-30-infrastructure-for-thinking.md`
carries `type: reflection`, `date: 2026-05-30`, `week: 2026-W22`). But that
destination sits in `wiki/` — a directory `RESOLVER.md` never mentions
(finding #5's blind spot) and one with no structural relationship to
`ideas/`, even though a reflection and an idea are the same kind of thing by
provenance: both self-generated, both produced rather than consumed. Adding
a resolver rule that still points at `wiki/personal/reflections/` (the
earlier version of this section) would close the routing gap but leave
produced knowledge scattered across two unrelated top-level directories for
no reason other than history.

The better fix is structural: make `ideas/` the vault's operational home for
*all* produced knowledge, and nest reflection under it —
`ideas/reflections/YYYY-MM-DD-slug.md` — alongside the existing flat
`ideas/<slug>.md` proposal files, rather than leaving reflection in `wiki/`
at all. This is consistent with how `ideas/` already handles variation
within itself: it doesn't split by domain via subdirectories, it uses `tags`
(`business-ideas`, `writing`, `job-search`) — so a `reflections/` subfolder
for the one shape of idea content that's dated and needs its own chronology
(mirroring `journal/YYYY/`) is a small, consistent extension, not a new
pattern. `RESOLVER.md` rule 7 becomes a single "is this produced (self-
generated) knowledge?" test with a shape-based sub-route: dated, first-
person, processing → `ideas/reflections/`; generative, proposing → flat
`ideas/<slug>.md`. `schema.md` would need its `reflection` destination
updated to match.

Worth being honest about the cost: unlike the routing-only fix, this
recommendation implies moving ~22 existing files (plus `index.md`) out of
`wiki/personal/reflections/` into `ideas/reflections/`, not just adding a
line to `RESOLVER.md`. That's a real migration with real link-rewrite work
(every `[[wiki/personal/reflections/...]]` reference updates), which is why
it belongs in a "recommendation" section rather than being treated as
free. It's still the correct end state — the alternative is a resolver rule
that's internally consistent but permanently papers over a location that
contradicts the taxonomy it's supposed to express.

The candidate fix the user floated first — `sources/reflections/`, on the
reasoning that a reflection is "another source of inputs" — still doesn't
hold up, for the same reason as before: every value in `schema.md`'s
`origin` enum (`transcript | article | twitter | export | manual`) describes
an *externally captured* channel, confirmed by a sampled file
(`sources/ai-resources.md`, `origin: manual`, a list of external links).
`sources/` protects one clean signal — externally-originated raw material —
and a reflection's provenance is the opposite: self-generated, and often
already a finished thought rather than raw material (the sampled file ends
"Whether this is the right investment of time or a form of productive
procrastination is genuinely open. Probably some of both" — that's
synthesis, not a transcript). `ideas/reflections/`, not `sources/
reflections/`, is where the provenance signal actually points.

Two smaller things surfaced while sampling reflection files, flagged but not
resolved here: they use a bare `## Compiled Truth` header with no Timeline,
despite being one-shot dated entries rather than accumulating entity pages —
a shape mismatch for `entity-model.md` to pick up, not `RESOLVER.md`; and
`reflections/index.md` uses `type: index`, a tenth frontmatter type never
enumerated in `schema.md` §4 — the same "usage outruns documented schema"
pattern as finding #1, just for a different type. Both travel with the files
if/when they move to `ideas/reflections/`.

**Consumed vs. produced as the concept/idea test.** Finding #4 noted that
rule 6 (concept) gets a precise test — "no owner, no due date" — while rule 7
(idea) gets only a category description, with no comparable test and no
`split` escape hatch. Reframing the split by *provenance* rather than by
*presumed future reusability* is not a new idea specific to this vault — it's
the same distinction several independent knowledge-management traditions
converge on: Zettelkasten's literature notes (what a source says) versus
permanent notes (your own synthesized thinking), and Tiago Forte's
Capture-Organize-Distill-**Express** in *Building a Second Brain*, where
"Express" is explicitly the produced/output half of the workflow. That
convergence is a reasonable signal the axis is doing real work, not just
relabeling.

It also fits the sampled data cleanly. Every sampled `concepts/` file
(`pen-testing-research`, `subsets`, `predictive-learning`,
`data-structures-overview`, `how-to-approach-a-system-design-interview-question`)
is a distillation of external material — a course, an interview guide, a
body of ML literature — and their `domain:` values (`ml`, `dsa`,
`interview-guides`) name external bodies of knowledge rather than personal
origination. Every sampled `ideas/` file (the business-idea files, the blog
post, `tweets.md`, the three `prompt-*.md` product specs) is self-originated
— nothing they reference comes from an external source. Applying "did this
come from something I read or heard, or did I generate it" resolves the
`prompt-*.md` ambiguity cleanly: it's a much lower-judgment test than "is
this reusable," which requires guessing at future use rather than checking a
fact about origin.

Reflection fits into the same frame as a third leaf, not a competing
category: produced knowledge that is dated and interior (processing what
happened) rather than generative and proposing (idea) or reusable and
timeless (concept, which stays consumed-first). Nesting it under
`ideas/reflections/` is that leaf made structural rather than just
analytical — one directory, `ideas/`, for produced knowledge, sub-routed by
shape, with `concepts/` remaining the parallel, unchanged home for consumed
knowledge. The third branch, once something produced becomes owned and
scoped, is promotion out of `ideas/` into `projects/` via the existing
`split` verb — extended to cover idea → project the same way it already
covers concept ↔ project — rather than three separately-justified,
unrelated categories.

One edge case worth naming and deliberately not solving: something
*produced* that is also *reusable/timeless* — a Zettelkasten "permanent
note," an original framework the author invented and intends to reuse —
would satisfy both rule 6's current test and the produced axis at once. No
sampled file in this vault is actually that case today (every concept is
consumed, every idea is produced-and-not-yet-reusable), so it's a real
future ambiguity, not a present one. Per this project's own bar — does this
clearly improve the target use case — it isn't worth a new category for a
zero-instance case; worth revisiting only if one shows up.

## Recommendations

Kept deliberately small, matching the "does this clearly improve local
knowledge work" bar this project holds itself to:

1. **Give `idea` and `organization` real frontmatter schemas** (even minimal
   ones — a `promoted_to`/`status` field for idea, nothing new for
   organization beyond a real `type: organization`). Otherwise the routing
   layer draws distinctions the shaping layer immediately erases.
2. **Add an idea/concept test symmetric to the existing concept/project
   test, based on provenance rather than presumed reusability**: did this
   originate from something external being distilled (→ `concepts/`), or was
   it self-generated (→ `ideas/`, produced knowledge, sub-routed by shape —
   `ideas/reflections/YYYY-MM-DD-slug.md` if dated/interior,
   `ideas/<slug>.md` if generative/proposing). This resolves the
   `prompt-*.md` ambiguity without new machinery, and gives `ideas/` the
   `split`-to-`projects/` escape hatch it currently lacks.
3. **Reorder or re-scope rule 8** so organization content isn't
   structurally unreachable behind rule 6 — e.g. move it before concept, or
   narrow rule 6's text to explicitly exclude career/org-structure content.
4. **Either build `inbox/` or delete the rule.** A resolver rule that refers
   to a directory that has never existed is worse than no rule — it implies
   a review workflow that isn't there.
5. **State what "single authority" actually covers.** Either extend the
   waterfall to the directories it's silent on, or narrow the claim. Given
   this project's own bias against speculative structure, narrowing the
   claim (and pointing the extra directories at `archive/` or leaving them
   as intentionally resolver-exempt scratch space) is the smaller change.
6. **Migrate `wiki/personal/reflections/**` into `ideas/reflections/**`**
   (files + `index.md` + inbound wikilinks) so produced knowledge has one
   home instead of two, and update `schema.md`'s `reflection` destination to
   match. Larger than the other items here — a file move plus a link
   rewrite, not a doc edit — so sequence it after the cheaper fixes above.

## Implications for `wakil`'s resolver

`ingest_service.py` already needs "resolver-driven routing" for both
proposed notes and stub entities (per `entity-model.md`'s entity-stubbing
section). The findings above argue for three concrete constraints on that
logic, independent of whatever specific vault it points at:

- **Don't implement first-match-wins over free-text category tests.**
  Finding #2 is a direct consequence of that structure. If `wakil` encodes
  a vault's resolver rules, a rule that's logically reachable only through
  the router's prior knowledge of the right answer isn't a rule worth
  encoding — surface it back to the user as an ambiguous case instead of
  silently picking the first textual match.
- **Model routing as 1:N, not 1:1.** Finding #8 is exactly the shape
  `IngestProposal`/`ProposedFile` already exists to handle (multiple
  proposed files from one ingest). The resolver step should produce a set of
  candidate destinations per extracted entity, not a single winner for the
  whole source.
- **Treat "no schema for this destination" as a hard stop, not a silent
  default.** Finding #1 shows what happens when a routing rule outruns its
  shaping rule: the type gets used inconsistently until someone notices by
  sampling files. `wakil` should refuse to propose a file into a `type:` the
  target vault's own `schema.md` doesn't define, and surface that as an
  explicit gap in the diff preview rather than writing a best-guess
  frontmatter block.
