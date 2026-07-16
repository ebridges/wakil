---
name: ingest-source
description: Orchestrate the end-to-end path from raw external material to a committed, linked, conformant knowledge-base note — normalize, capture, enrich, resolve entities, route, and conform — resuming by source id on failure. Use whenever asked to ingest, capture, or process external material into the knowledge base.
skill_api: 1
---

# ingest-source

This is the top-level, user-facing workflow. It does not do its own content
judgment — it decides which of the six focused skills below apply to the
material in front of it, in what order, and gets out of the way once each
has done its job. If you catch yourself deciding what a note should say,
which entity a mention resolves to, or where a file belongs, stop — that
judgment belongs to `content-synthesis`, `entity-resolution`, or
`note-routing` respectively, not to this skill.

## The two-command backbone

For the three shapes `wakil` ingests natively, the whole pipeline is two
commands:

```text
wakil ingest {transcript,text,article}     step 1: capture (deterministic, no model)
wakil enrich <source-id>                   step 2: analyze and link (model-driven DAG)
```

`wakil ingest` writes the raw material under `sources/` and records a
`Source` row, nothing more — no interpretation happens at this step.
`wakil enrich <source-id>` then runs a fixed, code-sequenced DAG
(`src/wakil/app/ingest_service.py`, `prepare_enrichment`):

1. **DAG node 1 — extraction.** Runs the CLI-native judgment from
   `skills/{transcript,text,article}/SKILL.md` (this is exactly what
   `content-synthesis` describes as already handled for native source
   types) and produces a proposed primary note.
2. **DAG node 2 — entity resolution.** Always invoked, never optional.
   Runs `skills/entity-resolve/SKILL.md` (the same judgment
   `entity-resolution` documents) and, for every entity it decides to
   create, writes a schema-routed stub page via `schema.directory` —
   the deterministic slice of what `note-routing` calls "entity types are
   code-owned."
3. **Validation gate.** `validate_proposal()` checks every proposed file
   carries schema-valid frontmatter for its `type:` and hard-stops on an
   unrecognized type or a duplicate path — a partial, mechanical slice of
   what `note-conformance` checks in full.

Then one preview, one confirm, one apply. Nothing is written before you
confirm; nothing partial is left behind if you decline.

## Where the automatic DAG stops — and why this skill exists beyond it

The DAG above gives you a schema-valid primary note plus schema-valid entity
stubs. It does **not**:

- write texture into a stub's State (Compiled Truth), decide which of the
  source's *other* mentions earn a back-link, or add Timeline entries beyond what
  extraction directly proposed — that's `entity-enrichment`'s job, per its
  own description of exactly this gap;
- route the primary note through the workspace's `RESOLVER.md` — the
  extraction skill picks the proposed note's path itself, so anything that
  needs workspace-specific subject-matter placement beyond what the
  extraction skill already decided is `note-routing`'s job;
- run the full no-slop prose bar, three-way slug consistency, or link-format
  audit — only frontmatter schema validity is checked automatically; the
  rest is `note-conformance`'s job.

So: for material where the automatic stub-and-scaffold output is genuinely
sufficient (a minor mention, a low-stakes capture), `wakil enrich` alone may
be enough. For anything that matters — a page worth real texture, a note
whose placement isn't obvious from the extraction skill's own guess, a
result you're about to commit — continue the workflow by hand through
`entity-enrichment` → `note-routing` → `note-conformance` before handing the
result to `kb-commit`. Use judgment on how far to go; don't assume the DAG's
automatic output is the finished product just because it applied cleanly.

## Dispatch: does the material need normalizing first?

| Material | Path |
|---|---|
| Meeting/call transcript file (`.txt`, `.md`, `.srt`) | CLI-native — `wakil ingest transcript` directly |
| Plain text, pasted note, clipping | CLI-native — `wakil ingest text` directly |
| Web article URL | CLI-native — `wakil ingest article` directly |
| PDF, voice note, YouTube video, scanned/OCR document, or anything else not already one of the three shapes above | Normalize first via `source-ingestion`, then continue through the same two-command backbone |

`source-ingestion` owns all of the per-source-type judgment for the
normalize-first column — what to extract, how to classify the result as
`transcript` vs. `text`, and how to build a `--context` string rich enough
for DAG node 1 to use. Don't re-derive that judgment here; hand the
material off and resume once it's normalized.

## Full workflow

```text
source-ingestion            (only when normalization is needed — see dispatch table)
        ↓
wakil ingest {shape}         capture: writes sources/*, deterministic
        ↓
wakil enrich <source-id>     DAG: content-synthesis (native judgment)
        ↓                         + entity-resolution + schema-valid stubs
entity-enrichment            texture, back-links, Timeline beyond the DAG's proposal
        ↓
note-routing                 placement for anything not already schema-routed
        ↓
note-conformance             full schema/slug/link/prose audit
        ↓
kb-commit                    reviewable diff, commit
```

Each stage's output is distinct, and that separation is the point — a
retry at any stage doesn't repeat the stages before it:

1. **`source-ingestion`** (conditional) → a normalized transcript/text file
   plus a `--context` string, ready for `wakil ingest`.
2. **`wakil ingest`** → the raw source note under `sources/`, recorded with
   a source id.
3. **`wakil enrich`** → a proposed primary note plus schema-valid entity
   stubs, previewed and applied together.
4. **`entity-enrichment`** → back-links, Compiled Truth texture, and
   Timeline entries the DAG didn't generate on its own.
5. **`note-routing`** → a destination directory and filename for anything
   the DAG's own extraction-skill guess didn't already settle.
6. **`note-conformance`** → a note that's schema-valid, consistently
   slugged, cleanly linked, and free of slop — ready to commit.
7. **`kb-commit`** → the actual commit, with a message and diff the user
   can review.

## Resuming a failed or interrupted run

Track progress by **source id**, not by re-running earlier stages:

- Capture is idempotent by content hash — re-running `wakil ingest` on
  material already captured reports the existing source id and writes
  nothing new. It is safe to re-run, but it does not advance a stalled
  pipeline; resume with `wakil enrich <source-id>` instead.
- If capture succeeded but `wakil enrich` failed, was declined at the
  confirmation prompt, or `validate_proposal()` blocked it on a schema
  issue — fix the underlying cause (an unrecognized entity type, a
  workspace schema gap) and re-run `wakil enrich <source-id>`. Don't
  re-capture; the source already exists.
- To re-analyze a source that already completed enrichment (new
  information surfaced, the first pass was thin), re-run
  `wakil enrich <source-id> --force`.
- If enrichment applied but the manual stages (`entity-enrichment`,
  `note-routing`, `note-conformance`) haven't run yet, pick up from
  whichever of those is next — each is safe to run on the DAG's output
  independent of how capture or enrichment went.

## Committing

`wakil ingest`/`wakil enrich` can commit their own output directly via
`--commit`/`--branch`/`--pr`, using the same `wakil source: ...` /
`wakil ingest: ...` message conventions `kb-commit` uses elsewhere. That's
appropriate when the automatic DAG output is the whole story. Once this
workflow continues past the DAG into `entity-enrichment`, `note-routing`,
or `note-conformance` — writing or moving files those flags don't know
about — skip the CLI's own commit flags and hand the full set of changes to
`kb-commit` for one reviewable commit instead of committing the DAG's
output separately from what came after it.

## Batch ingestion

Ingesting many items in one pass — a folder of PDFs, an inbox of captured
links — is `source-ingestion`'s test-3-5-before-bulk discipline applied at
the orchestration level too: run the full workflow above end-to-end on a
handful of items first, inspect the committed result, and only then repeat
it across the rest. A classification or routing bug caught on 3 items is a
fix; the same bug applied silently across 300 is a rebuild.
