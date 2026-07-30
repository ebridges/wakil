---
title: Knowledge Base Conventions
status: living
audience: wakil users and design
---

# Knowledge Base Conventions

`wakil` doesn't require a specific knowledge-base layout, but it can take
advantage of one when present. This describes the conventions it's designed
around — useful both for a new user setting up a workspace and for anyone
extending `wakil`'s routing/shaping logic.

This content originally lived in `PROMPT.md` (the project's initial planning
doc); it's kept here because it's still accurate user-facing guidance, unlike
most of that document's build-phase planning, which the shipped code and
`docs/adr/*` have since superseded.

## Example structure

A representative knowledge base:

```text
.
├── bin
│    ├── qmd-reindex.sh
│    └── qmd-setup.sh
├── companies
├── concepts
├── drafts
├── journal
│    ├── 2025
│    ├── 2026
│    ├── reflections
│    └── undated
├── media
│    └── articles
├── meetings
│    ├── 2025
│    └── 2026
├── people
├── projects
├── sources
│    ├── articles
│    ├── audio
│    ├── clippings
│    ├── messages
│    ├── screenshots
│    ├── transcripts
│    ├── tweets
│    └── videos
├── AGENTS.md
├── README.md
└── RESOLVER.md
```

## Directory expectations

| Path | Purpose |
| --- | --- |
| `bin/` | Local helper scripts for knowledge-base maintenance, such as QMD setup and reindexing. `wakil` may call these when explicitly requested, but should not assume every workspace has identical scripts. |
| `companies/` | Durable notes about companies, organizations, vendors, employers, prospects, or institutions. |
| `concepts/` | Evergreen concept notes — often the most important area for synthesis and linking. |
| `drafts/` | Work-in-progress writing, incomplete notes, rough outlines, and material not yet promoted into durable knowledge. Also where a proposed note falls back to when routing is unclear or its path collides. |
| `journal/` | Chronological personal or work notes. Useful for recency-aware queries and understanding how ideas evolved over time. |
| `journal/YYYY/` | Year-specific journal entries. |
| `journal/reflections/` | Higher-level reflections that may cut across daily or dated entries. |
| `journal/undated/` | Journal-like material without a clear date. |
| `media/articles/` | Curated or durable article notes, summaries, or responses — distinct from raw ingested article sources. |
| `meetings/` | Meeting notes organized by year: decisions, follow-ups, participants, project context. |
| `people/` | Durable notes about people — resolving names, relationships, meetings, organizations, and collaboration history. |
| `projects/` | Project notes, plans, decisions, status summaries, and related working material. |
| `sources/` | Raw or lightly processed source material brought into the knowledge base by `wakil ingest`. |
| `sources/transcripts/` | Meeting, call, video, or audio transcripts (including `.whisper` zip archives). |
| `sources/articles/` | Raw article captures, extracted article text, or source metadata. |
| `sources/clippings/`, `sources/messages/`, `sources/screenshots/`, `sources/tweets/`, `sources/videos/`, `sources/audio/` | Other raw capture kinds, as they're supported. |

The basic distinction:

```text
sources/     = raw or lightly processed input material
drafts/      = working material
concepts/    = durable evergreen ideas
people/      = durable person/entity notes
companies/   = durable organization notes
projects/    = durable project context
meetings/    = structured meeting knowledge
journal/     = chronological personal/work context
```

## Top-level Markdown files

| File | Purpose |
| --- | --- |
| `README.md` | Human-facing overview of the knowledge base — what it contains and how it's organized. `wakil init` detects and indexes it as high-priority workspace context. |
| `AGENTS.md` | Instructions for AI agents operating in this knowledge base (behavioral rules, writing conventions, safety constraints). `wakil init` detects and indexes it as high-priority context; it is not yet consulted by `wakil enrich`'s routing/shaping logic directly — see [issue #160](https://github.com/ebridges/wakil/issues/160). |
| `RESOLVER.md` | The authority for **where** knowledge belongs: whether material should become a person, company, meeting, project, concept, journal entry, source, or sensitive note — especially the judgment calls a fixed schema can't express (subject-matter routing, sensitivity overrides, ambiguity resolution). `wakil enrich` loads and includes it (capped at 4,000 characters, see `docs/TROUBLESHOOTING.md`) so proposed notes follow the workspace's own routing rules. |

Historically this table also included `SCHEMA.md` (page *shape*: frontmatter
fields, filename conventions, template structure) as RESOLVER.md's
counterpart. `wakil` no longer reads a workspace `SCHEMA.md` at all —
`docs/adr/0011-retire-schema-md-dependency.md` retired it in favor of driving
page shape structurally from the entity-schema catalog
(`schema/entities/*.yaml`, inspectable via `wakil schema list`), which can't
drift from what the code actually validates the way a hand-authored
Markdown doc could. If your knowledge base still has a `SCHEMA.md` from
before this change, it's inert as far as `wakil` is concerned — routing
still comes from `RESOLVER.md`.

## Routing before shaping

The correct note-creation sequence `wakil enrich` follows:

1. Load `RESOLVER.md` (if present) — decide where the knowledge belongs.
2. Load the entity-schema catalog — select the right page schema for that
   destination's type.
3. Propose the note (or a stub, for a newly-discovered entity) according to
   that schema.
4. Show the diff for review before writing anything.

| Input | Resolver decision | Schema decision |
| --- | --- | --- |
| A new person mentioned in a transcript | `people/first-last.md` | `type: person`, `name: First Last` |
| A raw transcript | `sources/transcripts/` | `type: source`, `origin: transcript` |
| A synthesized meeting note | `meetings/<date>-topic.md` | `type: meeting`, attendees, decisions |
| A reusable mental model | `concepts/<slug>.md` | `type: concept`, aliases, domain |
| A time-bound work effort | `projects/<scope>/<slug>.md` | `type: project`, status, owner |

Conventions `wakil` follows when creating or shaping a note:

- Filenames are stable entity identifiers: lower-case kebab-case.
- The Compiled Truth section holds current synthesized state; the
  Timeline / Log section is append-only evidence history (see
  [Entities: compiled pages](../README.md#entities-compiled-pages)).
- Internal links use Obsidian-style `[[wikilinks]]`, root-relative rather
  than relative paths.
- Attachments sit next to the owning note in a sibling folder with the same
  slug.
- Sensitive content (assessments, feedback, compensation) is flagged rather
  than surfaced by default in summaries, exports, or shared contexts.

The practical rule: **when routing is unclear, propose — don't guess. When
schema is clear, conform. When content is sensitive, protect.**
