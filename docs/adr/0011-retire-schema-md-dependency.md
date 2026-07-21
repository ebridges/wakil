---
title: Retire code dependency on workspace SCHEMA.md; derive frontmatter template from the entity-schema catalog
status: accepted
date: 2026-07-21
audience: wakil design
---

## Context

`SCHEMA.md` was one of `wakil`'s `SPECIAL_FILES` (`src/wakil/config/settings.py`)
and had two independent code consumers, both in `src/wakil/app/ingest_service.py`:

1. `load_workspace_guides()` read `SCHEMA.md` and `RESOLVER.md` verbatim
   (each capped at `GUIDE_MAX_CHARS = 4,000` chars) and dumped both into the
   extraction/resolution prompts as "Workspace guidance from SCHEMA.md (page
   shape and metadata): ...". This was genuinely redundant: `src/wakil/llm/
   prompts.py`'s `describe_entity_types_full`/`describe_entity_types` already
   mechanically render directory/category/page_shape/required-and-optional
   fields per entity type from `load_entity_schemas()`
   (`src/wakil/schema/loader.py`) into the same prompts. A hand-authored
   `SCHEMA.md` was just duplicating — and risking drifting from — what the
   YAML catalog under `src/wakil/schema/entities/` already states
   structurally, with no validation tying the two together.

2. `transcript_frontmatter_template()` regex-scraped `SCHEMA.md` at *capture*
   time for a fenced yaml block under a heading mentioning "transcript" or
   "source", and used whatever it found (or a hardcoded two-field fallback
   when it found nothing) to shape a raw transcript capture's frontmatter.
   This was a second, independent mechanism from (1) — a freeform, regex-based
   parse of prose Markdown, with no schema validation of its own output.

Both consumers depended on the same class of problem: a document a user has
to hand-author and keep in sync with `wakil`'s own schema catalog, parsed
either verbatim (1) or by regex (2), with no guarantee the two ever agree.
`docs/TROUBLESHOOTING.md` already documents two concrete failures downstream
of this design — a silent 4,000-char truncation with no warning, and
non-transcript captures whose hardcoded frontmatter fields don't match a
real vault's `SCHEMA.md`-documented schema.

`RESOLVER.md` is explicitly **not** addressed by this decision. It plays a
different, richer role: subject-matter subdirectory routing for
document-category content (raw sources, synthesized notes, journal entries,
meeting records) — content that doesn't resolve to a single entity `type`
and therefore has no `schema.directory` to route by. `src/wakil/skills/
note-routing/SKILL.md` draws this boundary explicitly ("RESOLVER.md → where
the note belongs... SCHEMA.md → how the note is shaped"), and a fixed YAML
schema catalog structurally cannot express subject-matter routing,
sensitivity overrides that pre-empt normal routing, naming/linking
conventions, or the ambiguity-resolution judgment `RESOLVER.md` is documented
to carry — this is genuine, code-can't-replace judgment, not duplicated
structure. `RESOLVER.md`'s own future (a built-in, overridable default to
replace today's from-a-blank-page authoring burden) is a separate,
already-proposed decision (`docs/adr/0012-resolver-md-migration-strategy.md`)
and is out of scope here; this ADR changes none of `RESOLVER.md`'s loading,
truncation, or rendering behavior.

## Decision

Retire `SCHEMA.md` as a code dependency; keep `RESOLVER.md` exactly as-is.

- `load_workspace_guides()` now reads only `RESOLVER.md`. Page shape and
  metadata guidance comes exclusively from the schema-catalog rendering
  (`describe_entity_types_full`/`describe_entity_types`) that was already
  doing this job structurally.
- `transcript_frontmatter_template()` no longer scrapes `SCHEMA.md`. It now
  derives an equivalent field template directly from the `source` entity
  schema (`src/wakil/schema/entities/source.yaml`): its base `fields` plus
  its `origins["transcript"]` sub-schema — the same effective-fields merge
  `wakil.schema.validate.validate_frontmatter` already performs for an
  `origin: transcript` note. `_KNOWN_FIELD_VALUES`/`_transcript_metadata`
  keep filling in the fields they know a real value for (title, meeting
  date, create date, origin kind, file url); the rest stay blank
  placeholders, same shape as before, just schema-sourced instead of
  regex-scraped.
- `SCHEMA.md` is removed from `SPECIAL_FILES`
  (`src/wakil/config/settings.py`), so `wakil status` no longer surfaces it
  as workspace context — nothing in `wakil` reads its content anymore, so
  flagging its presence would be misleading.
- `RESOLVER.md`'s loading, truncation (`GUIDE_MAX_CHARS`), and prompt
  rendering are untouched.

## Consequences

- A workspace no longer needs to hand-author or maintain a `SCHEMA.md` for
  `wakil` to produce schema-correct transcript frontmatter or a
  schema-accurate extraction prompt — the entity-schema catalog is the
  single source of truth, and a kb-local/user override of that catalog
  (`schema/entities/`, already-supported precedence) is the one remaining
  way to customize the shape.
- Transcript captures now consistently carry every field the `source` schema
  defines for a `transcript` origin (including an explicit `type: source`
  line), not just the two fields (`created`, `meeting_date`) a workspace
  without a `SCHEMA.md` used to get by default.
- One less silent-truncation surface: `GUIDE_MAX_CHARS` now only applies to
  `RESOLVER.md`.
- `RESOLVER.md`'s own convention-document burden is unchanged by this PR —
  see `docs/adr/0012-resolver-md-migration-strategy.md` for that separate,
  still-open decision.

## Implementation

- **PR #21** — "Retire code dependency on workspace SCHEMA.md; derive frontmatter template from the entity-schema catalog"

## Sources

- `src/wakil/app/ingest_service.py` (`load_workspace_guides`,
  `transcript_frontmatter_template`, `_KNOWN_FIELD_VALUES`,
  `_transcript_metadata`)
- `src/wakil/llm/prompts.py` (`describe_entity_types_full`,
  `describe_entity_types`)
- `src/wakil/schema/loader.py` (`EntitySchema`, `FieldSpec`,
  `load_entity_schemas`)
- `src/wakil/schema/entities/source.yaml`
- `src/wakil/skills/note-routing/SKILL.md`
- `docs/TROUBLESHOOTING.md`, "Workspace guide file (RESOLVER.md) is silently
  truncated at 4,000 chars" and "Ingested source frontmatter fields don't
  match the vault's schema"
- `docs/adr/0012-resolver-md-migration-strategy.md` (proposed, not merged as
  of this ADR — documents why `RESOLVER.md` is out of scope here)
