---
title: Strategy for a built-in, overridable default RESOLVER.md
status: proposed
date: 2026-07-21
audience: wakil design
---

## Context

`RESOLVER.md` is one of `wakil`'s recognized `SPECIAL_FILES`
(`src/wakil/config/settings.py:33`) and is read verbatim, capped at
`GUIDE_MAX_CHARS` (4,000 chars), by `load_workspace_guides()`
(`src/wakil/app/ingest_service.py:1261-1271`) whenever `wakil enrich` builds
its extraction/resolution prompt (`ingest_service.py:384`). Unlike entity
schemas and skills, this read has **no precedence chain at all** today: it is
a flat `config.root_path / "RESOLVER.md"` lookup. If the file is absent,
`guides["RESOLVER.md"]` is simply missing — there is no fallback, default, or
built-in content of any kind. Every workspace starts from a blank page for
this judgment.

### What RESOLVER.md is documented to carry, and why a schema catalog can't

`src/wakil/skills/note-routing/SKILL.md` draws the boundary explicitly: entity
routing (`schema.directory` per type, `src/wakil/schema/entities/*.yaml`) is
"code-owned... not a judgment call," while "everything else is
workspace-owned, via `RESOLVER.md`" — raw sources, synthesized notes, journal
entries, meeting records, "and anything else that isn't a single resolved
entity." The skill spells out four things a fixed YAML schema catalog
structurally cannot express, because they are not about a single entity
`type` at all:

1. **Subject-matter subdirectory routing for document-category content.**
   `EntitySchema.directory` (`src/wakil/schema/loader.py`) is one static
   string per `type` — fine for "a `person` always goes to `people/`," but
   document-category content (a raw source, a synthesized note, a journal
   entry) doesn't have a `type` in that sense; its destination is a judgment
   about *subject matter*, evaluated against a workspace's own vocabulary of
   subject directories. `docs/entity-resolution.md`'s worked example vault
   shows this concretely: a ten-rule waterfall over categories (`people/`,
   `companies/`, `meetings/`, `projects/`, `concepts/`, `ideas/`,
   `organization/`, `journal/`, `sensitive/`, `archive/`) that a static
   per-type directory field cannot encode, because the same raw input (a
   transcript) can resolve to different destinations depending on what it's
   *about*, not what shape it is.
2. **Sensitivity overrides that pre-empt normal routing.** The worked
   example's rule 1 ("sensitive → `sensitive/` — stop") short-circuits every
   later rule. `note-routing/SKILL.md`'s own "Sensitive content" section
   treats this as a `RESOLVER.md`-declared "sensitive-content section," not
   a schema field — schemas do carry a `sensitive: true` flag per *type*
   (see `assessment.yaml`), but a workspace's override list of sensitive
   *topics or subjects that cut across types* is not expressible as a
   per-type schema property.
3. **Naming/linking conventions.** `note-routing/SKILL.md`'s "Naming
   defaults" section (kebab-case, qualifier suffixes for slug collisions,
   leading ISO dates for dated occurrences) and `docs/entity-resolution.md`'s
   "linking rule" (absolute vault-root paths, never relative) are
   workspace-wide conventions about *filenames and links*, not per-type
   frontmatter shape — there is no schema field for either.
4. **Ambiguity-resolution judgment patterns** — instructional/timeless vs.
   productive/time-bound, raw vs. synthesized — that `note-routing/SKILL.md`
   documents as *default reasoning templates* to fall back on only when a
   workspace's own `RESOLVER.md` doesn't already settle the case. These are
   prose heuristics for a human-judgment call, not data a schema loader
   validates against.

`docs/entity-resolution.md`'s critical read of the worked-example
`RESOLVER.md` also documents where this authority breaks down in practice
(routing targets with no matching schema, a waterfall that structurally
pre-empts a later rule, an authority that claims completeness but is silent
on roughly half the vault's directories, a fallback directory that doesn't
exist on disk). None of these are `wakil` engine bugs — they are the
predictable cost of every workspace inventing this document from a blank
page with no worked reference to check itself against. That cost is exactly
what shipping an opinionated built-in default would reduce, without taking
away the ability to override it.

### Two Hermes files as candidate migration content

`/Users/ebridges/Projects/kb/.hermes/skills/_brain-filing-rules.md` and
`_output-rules.md` are the repo owner's own filing/output conventions from a
different, personal agent system ("Hermes"), not written for `wakil`. Read
directly, they split cleanly into two populations:

**Generically portable knowledge-base conventions** (worth becoming `wakil`
defaults):

- *Primary-subject filing, not format/source.* `_brain-filing-rules.md`'s
  "Decision Protocol" ("PRIMARY SUBJECT... determines where it goes. Not the
  format, not the source, not the skill that's running") and its
  misfiling-pattern table are the same rule `note-routing/SKILL.md` already
  states independently ("Filing principle: primary subject, not format or
  source"). This is strong convergent evidence the rule is genuinely
  workspace-agnostic, not Hermes-specific — it's worth keeping as the anchor
  rule of a built-in default, expressed in `wakil`'s own words (it already
  is, in the skill prose; the gap is a *worked default routing table* to
  pair with it).
- *Sui generis synthesis exception.* `_brain-filing-rules.md`'s "Sanctioned
  exception" section is, almost verbatim, the same exception
  `note-routing/SKILL.md` already documents ("Exception — sui generis
  synthesis"). Worth carrying into a default `RESOLVER.md`, but scoped
  generically (a `synthesis/<slug>` or workspace-equivalent home) rather than
  Hermes's `media/<format>/<slug>-personalized.md` shape, which bakes in a
  `media/` top-level directory `wakil` has no reason to assume exists.
- *Notability gate.* `_brain-filing-rules.md`'s "Notability Gate" section is
  reproduced near word-for-word in `note-routing/SKILL.md`'s own "Notability
  gate" section already. No migration needed — it's already `wakil`'s
  default; flagging it here only to confirm the two documents agree, which
  is further evidence this rule belongs at the "default convention" layer
  rather than being workspace-specific.
- *"What sources/ is actually for" as a negative-space rule.* The rule that
  a raw-import directory holds only genuinely uninterpreted bulk
  data/captures, not anything with a clear subject, is the same "Raw vs.
  synthesized" pattern `note-routing/SKILL.md` already carries as a default
  judgment template. Worth keeping, generalized away from the specific
  `sources/` directory name.

**Hermes-specific mechanics that should not be copied** (different tool,
different constraints):

- *Iron Law: Back-linking, with a mandated inline citation/back-link
  syntax.* This assumes a specific vault convention (`[Source: ...]` tags,
  a `Timeline`/`See Also` append target) that overlaps with, but doesn't
  match, `wakil`'s own entity page shape
  (`docs/entity-model.md`'s Compiled Truth/Timeline convention, already
  implemented independently). Backlinks in `wakil` are a live SQLite query
  over a relationship table (ADR 0006), not an author-maintained inline
  citation block — importing Hermes's citation-format mandate would
  conflict with a mechanism `wakil` already committed to elsewhere. Not
  portable as written.
- *Raw source preservation via `gbrain files upload-raw`, `.raw/`
  sidecars, Supabase/S3 cloud-storage routing, `.redirect.yaml` pointers.*
  This is Hermes-tool-specific infrastructure (a `gbrain` CLI, TUS resumable
  upload, a specific cloud-storage backend) with no `wakil` analogue and no
  reason to grow one — `wakil`'s design biases explicitly avoid "remote
  runtimes" and prefer git-native storage. Entirely out of scope.
- *Dream-cycle synthesize/patterns allow-list, `_brain-filing-rules.json`,
  reflections/ideas/patterns/dream-cycle-summaries paths.* This is a
  Hermes-specific automated background synthesis feature ("dream cycle")
  `wakil` has no equivalent of and, per `CLAUDE.md`'s explicit "Avoid:
  hidden background behavior," should not grow. Not portable.
- *Takes attribution contract (holder/subject, weight grid, "so what"
  test).* A Hermes-specific structured-claims subsystem
  (`gbrain:takes:begin` fences, a weight-calibration grid) with no `wakil`
  concept to anchor it to. Not portable.
- *Deterministic-links rule in `_output-rules.md`.* Generically good advice
  ("never guess a URL or path... build it from the slug") but the concrete
  syntax (`[abc1234](https://github.com/{owner}/{repo}/commit/abc1234)`)
  is bound to Hermes's own commit-linking convention. The underlying
  principle — links are built from data, not invented — is worth restating
  in `wakil`'s own terms (arguably a `note-conformance` concern, not
  `RESOLVER.md`'s, since it's about link construction inside a page, not
  where the page goes) rather than copied.
- *No-slop / exact-phrasing-preservation / title-quality rules
  (`_output-rules.md`).* These are real, good conventions, but they govern
  **page shape and prose quality**, which `note-routing/SKILL.md` explicitly
  hands off to `note-conformance` ("`SCHEMA.md` → how the note is shaped").
  They don't belong in a routing default at all — migrating them (if ever)
  belongs in a `note-conformance`/`SCHEMA.md`-scoped follow-up, not this
  one.

## Proposed decision

Ship `wakil` with a **built-in, opinionated default `RESOLVER.md`**, using
the same kb-local/user/built-in, first-match-wins, whole-file precedence
pattern already established for skills (ADR 0001,
`src/wakil/skills/resolver.py`) and reused for entity schemas
(`src/wakil/schema/loader.py`'s module docstring: "Resolution mirrors
`wakil.skills.resolver`... applied per type-file instead of per
skill-directory"). This ADR proposes the mechanism for review; **it does not
implement it.**

### Where the built-in default would live

A new file, `src/wakil/skills/note-routing/RESOLVER.md` (co-located with the
skill that consumes it, mirroring how built-in skills already carry their
own reference material under their skill directory) or, if a workspace-guide
concept deserves its own package location distinct from skills,
`src/wakil/config/defaults/RESOLVER.md`. Content: the generically-portable
rules identified above (primary-subject filing, sui-generis-synthesis
exception, notability gate, raw-vs-interpreted negative space,
instructional/timeless-vs-time-bound judgment pattern) expressed as a
*default* routing table over a small, deliberately generic set of subject
buckets (`people/`, `companies/`, `concepts/`, `projects/`, `sources/`,
`journal/`, `sensitive/`) that roughly track `wakil`'s own built-in entity
`directory:` values already declared in `src/wakil/schema/entities/*.yaml`,
so the built-in default and the built-in schema catalog agree with each
other out of the box.

### Extending the resolver pattern vs. a lighter mechanism

Two candidate mechanisms, evaluated against this project's "does this
clearly improve local knowledge work" bar:

**Option A — reuse `resolve_skill`'s full machinery.** Treat
`RESOLVER.md` as a one-file "skill-shaped" resource resolved through
`wakil.skills.resolver.resolve_roots` (or a thin wrapper around it) against
the same four-tier root order (`WAKIL_SKILL_PATH`-equivalent override,
`<kb-root>/`, user-config, built-in). This buys the exact-same "invalid
override blocks fallback rather than silently degrading" guarantee ADR 0001
already established (spec §9.2), for free, and needs no new invariant to be
independently reasoned about.

**Option B — a narrower, purpose-built lookup**, matching entity schemas'
per-file precedence more closely than skills' per-directory precedence,
since `RESOLVER.md` is a single file, not a directory of supporting assets:
search an ordered list of candidate paths (`<kb-root>/RESOLVER.md` →
user-config `~/.config/wakil/RESOLVER.md` → the built-in default under
`src/wakil/skills/note-routing/`), first file found wins, no merging. This
is a much smaller diff — a handful of lines in or near
`load_workspace_guides` — and doesn't need a `SkillRoot`/`ResolutionContext`
detour for a single Markdown file with no manifest, no `SKILL.md`
frontmatter, and no `skill_api` version to validate.

**Recommendation for the eventual implementation PR: Option B.** The
skill resolver's extra machinery (per-directory validation, frontmatter
parsing, `skill_api` version checks, whole-*directory* loading) exists to
solve problems `RESOLVER.md` doesn't have — it's one file, with no
supporting assets and no metadata contract to validate. Reusing
`resolve_skill` wholesale would mean inventing a fake `SKILL.md` wrapper
around `RESOLVER.md` just to satisfy a validation path that doesn't apply.
Option B keeps the same three properties this project has already decided
are worth having (kb-local overrides win, first-match-wins, no merging) at
a complexity level proportional to a single-file resource — consistent with
`CLAUDE.md`'s "keep the implementation simple unless added complexity has a
clear and self-evident impact." Whether a "missing/invalid override blocks
fallback" rule (ADR 0001's spec §9.2 behavior) is worth carrying over for a
single Markdown file with no structural validation to fail is an open
question for the implementation PR — with no frontmatter contract to check,
"invalid" mostly reduces to "unreadable," so it may be moot in practice, but
it should still be settled once code is written, since the choice has to
mean something once a kb-local override exists but the file can't be read.

### Interaction with `load_workspace_guides`

Today, `load_workspace_guides()` reads `RESOLVER.md` only from
`config.root_path` and silently omits the key when absent (no fallback,
`guides` dict is simply missing `"RESOLVER.md"`). Under this proposal, the
read path changes from "check one path, get nothing on miss" to "resolve
across the precedence chain, get the built-in default on a clean miss":

- `load_workspace_guides` would call the new lookup (Option B above) instead
  of doing its own `path.is_file()` check for `RESOLVER.md` specifically —
  `SCHEMA.md`'s read stays exactly as it is today (no built-in default is
  proposed for `SCHEMA.md`; this ADR is scoped to `RESOLVER.md` only).
- The returned dict would always carry a `"RESOLVER.md"` entry once this
  ships (either the kb-local override or the built-in default), so
  `note-routing/SKILL.md`'s "If the workspace has no RESOLVER.md... ask the
  user" branch would only ever fire on a *load failure* of the built-in
  default itself (a packaging bug), never on a workspace simply not having
  authored one — that branch's prose would need a matching update in the
  same implementation PR, since its current framing ("no RESOLVER.md
  present → ask") stops being the reachable case once a built-in always
  resolves.
- `GUIDE_MAX_CHARS` truncation (4,000 chars) still applies to whichever file
  wins — the built-in default would need to be written under that budget
  (the two Hermes source files are ~5.5KB and ~1.3KB combined; the portable
  subset identified above is much smaller than either), including some
  margin, since `SCHEMA.md`'s guide competes for the same prompt budget.
- No change is proposed to `SPECIAL_FILES` in `src/wakil/config/settings.py`
  — it lists files `wakil` treats as high-priority workspace context *when
  present*, which remains an accurate description of kb-local
  `RESOLVER.md`; a built-in default living in the package isn't a "special
  file in the workspace" and doesn't need to appear there.

## Consequences (if accepted and implemented)

- A workspace with no `RESOLVER.md` at all gets usable primary-subject
  routing, a sui-generis-synthesis exception, and a notability gate out of
  the box, instead of `note-routing/SKILL.md`'s current "ask the user" hard
  stop on every ambiguous case — significantly lowering the setup cost this
  ADR's Context section documents.
- A workspace can still fully override the default (drop its own
  `RESOLVER.md` at the kb root) with no merging semantics to reason about,
  matching the precedent ADR 0001 already set: override or don't, no
  partial inheritance.
- `note-routing/SKILL.md`'s "ask the user" branch (Step 2 of its decision
  tree) becomes unreachable for the "no RESOLVER.md" case and needs prose
  changes alongside the code change, or the skill and the engine behavior
  will disagree.
- The built-in default becomes a second piece of `wakil`-maintained prose
  (alongside built-in skills) that needs its own upkeep as routing judgment
  evolves — a small, ongoing maintenance surface, but a bounded one (single
  file, no merge-drift risk per ADR 0001's precedent).
- This does not resolve `docs/entity-resolution.md`'s findings about the
  worked-example vault's own `RESOLVER.md` (unreachable rule 8, undefined
  `idea`/`organization` schemas, a nonexistent `inbox/` fallback, etc.) —
  those are that specific workspace's authored content, not something a
  built-in default fixes. A built-in default only helps a workspace that
  starts from nothing; it doesn't audit or repair an existing hand-authored
  file.
- No code, tests, or built-in `RESOLVER.md` content are introduced by this
  ADR. This is a proposal for review; implementation is a separate,
  follow-up PR once the mechanism (Option A vs. B) and default content are
  agreed.

## Implementation

Not yet started. This ADR is the proposal; the repo owner is expected to
review the mechanism choice (Option B recommended) and the migration list
above before any implementation PR is opened. This document itself was
authored and submitted as PR #19 (docs-only, no code changes).

## Sources

- `src/wakil/skills/note-routing/SKILL.md` — routing authority split
  ("Entity types are code-owned" vs. "Everything else is workspace-owned,
  via RESOLVER.md"), "Filing principle: primary subject, not format or
  source," "Notability gate," "Default judgment patterns for hard filing
  calls," "Sensitive content," "Naming defaults," decision tree.
- `docs/entity-resolution.md` — worked-example `RESOLVER.md`'s ten-rule
  waterfall table, "What works," "Where it breaks" findings #1-#9,
  "Implications for wakil's resolver."
- `src/wakil/skills/resolver.py` — `resolve_roots`/`resolve_skill`
  implementation of kb-local/user/built-in, first-match-wins,
  whole-directory precedence.
- `docs/adr/0001-skill-resolution-precedence-first-match-wins.md` — the
  precedent this proposal extends: first-match-wins, no merging, invalid
  override blocks fallback (spec §9.2).
- `src/wakil/schema/loader.py` — module docstring documenting the same
  precedence pattern already reused for entity-schema resolution
  ("Resolution mirrors `wakil.skills.resolver`... applied per type-file
  instead of per skill-directory").
- `src/wakil/app/ingest_service.py` — `load_workspace_guides()` (current
  flat, no-fallback `RESOLVER.md`/`SCHEMA.md` read) and `GUIDE_MAX_CHARS`
  (4,000-char truncation).
- `src/wakil/config/settings.py` — `SPECIAL_FILES` tuple.
- `/Users/ebridges/Projects/kb/.hermes/skills/_brain-filing-rules.md` —
  Hermes filing conventions (primary-subject rule, misfiling table,
  sui-generis-synthesis exception, notability gate, `sources/` scope, Iron
  Law back-linking, raw-source-preservation/`gbrain files upload-raw`,
  dream-cycle allow-list, takes-attribution contract).
- `/Users/ebridges/Projects/kb/.hermes/skills/_output-rules.md` — Hermes
  output conventions (deterministic links, no-slop, exact-phrasing
  preservation, title quality).
