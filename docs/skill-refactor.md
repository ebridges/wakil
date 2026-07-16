# Skill refactor

Build a catalog of extensible skills useful for managing ingest and maintenance of a knowledge base in markdown format.

## Goal

I have a set of skills (housed at `~/Projects/kb`) that works overall pretty well for ingesting raw sources, doing interpreted synthesis of them into usable content, and enriched through linkages with existing entities in the KB.

However, that set of skills has diverged and evolved to a point where it contradicts itself and can fail silently.  It is also intimately connected to the `gbrain` tool, and which I want to move away from.  This is the fundamental motivation for switching to create `wakil`.

I'd like to take that set of skills and transform them into a set of skills for `wakil` that retains the deep expertise already built in there, while fixing issues of rot, duplication, contradiction, and coupling with legacy tools (e.g. `gbrain`).

The existing system also provides core functionality that gets consistently overridden as Hermes (my agent orchestrator) evolves skills by updating these skills in-place. This is a key source of the rot I'm experiencing.

I'd like to introduce in `wakil` a core set of "golden" skills and best practices that can be overridden by a user or by hermes.

---

## Core skills

This is the set of core skills that the `wakil` kb should support.  The key functionality should be captured from `~/Projects/kb/.hermes/skills`, after refactoring and fixing of the issues outlined below and then routed into one (or more) of the skills outlined below.  The Hermes skills contain a lot of useful nuance after having evolved and this should not be lost, just normalized and abstracted away from `gbrain`.

1. `ingest-source` — Coordinates the complete workflow for bringing external material into the knowledge base.
2. `source-ingestion` — Retrieves and normalizes raw source content and its provenance.
3. `content-synthesis` — Produces a standalone interpretive note from source material.
4. `entity-resolution` — Matches referenced people, companies, projects, and concepts to existing entities.
5. `entity-enrichment` — Adds meaningful entity links and creates missing entities where appropriate.
6. `note-routing` — Determines the correct note type, filename, and location using `RESOLVER.md`.
7. `note-conformance` — Applies and validates note structure and metadata using `SCHEMA.md`.
8. `knowledge-query` — Answers questions by translating them into effective QMD retrievals.
9. `knowledge-research` — Investigates a question across the KB and external sources with traceable evidence.
10. `note-revision` — Incorporates new information into existing notes without discarding useful prior knowledge.
11. `skill-authoring` — Creates, overrides, and validates skills without modifying built-in versions.
12. `kb-commit` — Reviews and commits KB changes using repository-specific validation and summaries.

### 1. `ingest-source`

`ingest-source` is the user-facing workflow for bringing external material into the knowledge base.

Responsibilities:

- coordinate source retrieval, synthesis, entity integration, routing, and conformance;
- select the appropriate ingestion behavior for the source type;
- preserve progress and surface failures at the stage where they occur;
- produce both a normalized source note and a synthesized knowledge note where appropriate.

`ingest-source` should orchestrate the underlying skills rather than duplicate their implementation.

A typical workflow is:

```text
source-ingestion
→ content-synthesis
→ entity-resolution
→ entity-enrichment
→ note-routing
→ note-conformance
→ persist notes
```

---

### 2. `source-ingestion`

`source-ingestion` retrieves or accepts external material and stores it in a normalized source-note format.

Responsibilities:

- identify the source type;
- retrieve or accept the raw content;
- preserve source metadata and provenance;
- normalize the content into a common source-note structure;
- hand the normalized source to `content-synthesis`.

Supported source types may include:

- meeting transcripts;
- web articles;
- reader applications such as Readwise;
- Kindle books and highlights;
- YouTube videos;
- Twitter/X posts and threads.

Source-specific implementations may later sit beneath this skill:

```text
article-ingestion
meeting-transcript-ingestion
youtube-ingestion
kindle-ingestion
readwise-ingestion
social-post-ingestion
```

The initial implementation should introduce specialized skills only where source handling differs enough to justify them.

---

### 3. `content-synthesis`

`content-synthesis` turns source material into a coherent standalone knowledge note.

Responsibilities:

- identify central themes, claims, conclusions, and implications;
- distinguish source statements from inferred conclusions;
- produce a readable note that does not require reopening the raw source;
- preserve traceability to the source material;
- extract decisions, questions, actions, insights, or arguments as appropriate;
- avoid reducing the output to a transcript summary or list of excerpts.

The same source may be synthesized differently depending on its purpose. A meeting transcript, for example, may produce a meeting record, decision note, project update, or reusable concept note.

The name `content-synthesis` is preferable to `source-synthesis` because the skill may later synthesize across multiple sources or existing notes.

---

### 4. `entity-resolution`

`entity-resolution` determines whether a referenced entity corresponds to an existing knowledge-base entity.

Responsibilities:

- search for candidate people, companies, projects, concepts, meetings, and other entities;
- compare names, aliases, relationships, and contextual evidence;
- return a confident match, no match, or an ambiguous result;
- avoid automatically merging uncertain identities;
- provide candidate information to `entity-enrichment`.

This skill handles identity matching only. It does not decide which links should be added to a note.

---

### 5. `entity-enrichment`

`entity-enrichment` integrates a note into the existing knowledge network.

Responsibilities:

- identify entities and concepts referenced by the note;
- call `entity-resolution` to match them against existing entities;
- add meaningful links to existing notes;
- propose or create missing entities where appropriate;
- avoid superficial or excessive linking;
- preserve ambiguity when entity identity is uncertain.

The distinction is:

```text
entity-resolution → determine what an entity refers to
entity-enrichment → decide how the note should connect to it
```

---

### 6. `note-routing`

`note-routing` determines where a note belongs according to `RESOLVER.md`.

Responsibilities:

- determine the note or entity type;
- select the appropriate destination directory;
- generate the canonical filename;
- identify ambiguous routing decisions;
- explain the selected route when the decision is not obvious.

This skill decides where a note belongs. It does not define the note’s internal structure.

---

### 7. `note-conformance`

`note-conformance` applies and validates the conventions defined in `SCHEMA.md`.

Responsibilities:

- construct or validate frontmatter;
- enforce naming conventions;
- apply the appropriate page structure;
- normalize links and stable identifiers;
- report or repair schema violations;
- preserve content while correcting structural inconsistencies.

The distinction between routing and conformance is:

```text
RESOLVER.md → where the note belongs
SCHEMA.md   → how the note is shaped
```

---

### 8. `knowledge-query`

`knowledge-query` answers questions using material already present in the knowledge base.

Responsibilities:

- translate a natural-language question into one or more effective QMD searches;
- select appropriate collections, contexts, filters, and retrieval strategies;
- inspect and combine the returned material;
- answer with traceable references to relevant notes;
- distinguish retrieved facts from inference;
- disclose when the available evidence is incomplete or conflicting.

QMD should remain an implementation detail:

```text
knowledge-query
    uses: qmd
```

This preserves the user-facing capability if the underlying retrieval engine changes later.

---

### 9. `knowledge-research`

`knowledge-research` investigates a question that requires broader analysis than a direct KB query.

Responsibilities:

- search the knowledge base for relevant prior material;
- use external sources when the task requires information not already present;
- identify supporting evidence, disagreements, and unresolved questions;
- distinguish known information from gaps and inference;
- produce a sourced research note or update an existing one;
- connect findings to relevant entities and concepts.

For the initial implementation, the skill may coordinate both KB and external research while clearly preserving source provenance.

A later implementation may split this capability into:

```text
kb-research
external-research
research-synthesis
```

That split should be deferred until the combined skill becomes difficult to operate or test.

---

### 10. `note-revision`

`note-revision` incorporates new information into an existing note without discarding valuable prior knowledge.

Responsibilities:

- identify the relevant existing note;
- compare new material with current content;
- preserve still-valid information;
- add complementary evidence;
- identify contradictions or superseded claims;
- distinguish additive updates from replacements;
- update timestamps and provenance;
- avoid replacing a developed note with a newly generated summary.

This skill is essential because a knowledge base must evolve existing knowledge, not only create new notes.

---

### 11. `skill-authoring`

`skill-authoring` creates and modifies skills for the knowledge-base system.

Responsibilities:

- create a new skill from a stated purpose;
- define a clear scope, inputs, outputs, and workflow;
- copy a built-in skill into an override location;
- inspect the currently resolved implementation;
- revise an existing local skill;
- validate skill structure and metadata;
- avoid modifying built-in skills directly.

Skill placement and search-path resolution should remain application responsibilities. The skill should call those capabilities rather than independently reproduce resolution logic.

A later supporting skill may be added:

```text
skill-evaluation
```

It could review skill scope, internal consistency, maintainability, and observed results, but it is not required for the first release.

That deferred intention is now fulfilled by the eval mechanism itself (per-skill `eval.json` scenarios, `tests/evals/runner.py`, and `wakil skills lint`) rather than by a 13th catalog skill.

---

### 12. `kb-commit`

`kb-commit` normalizes Git commits around knowledge-base-specific concerns.

Responsibilities:

- inspect the working-tree changes;
- exclude transient, generated, or sensitive files;
- validate changed notes where appropriate;
- summarize the knowledge changes rather than merely listing modified files;
- construct a useful commit message;
- show the proposed commit contents;
- commit only after explicit user approval.

This skill should focus on making the repository history understandable as a history of knowledge changes.

---

## Architecture

The first-release skill set is organized into four layers.

### Workflow orchestration

```text
ingest-source
```

This layer exposes a convenient end-to-end operation while delegating work to focused skills.

### Knowledge transformation

```text
source-ingestion
content-synthesis
entity-resolution
entity-enrichment
knowledge-query
knowledge-research
note-revision
```

These skills retrieve, interpret, connect, query, research, and update knowledge.

### Repository governance

```text
note-routing
note-conformance
```

These skills apply the repository’s organizational and structural rules.

### System operation

```text
skill-authoring
kb-commit
```

These skills manage the behavior of the system and the lifecycle of repository changes.

---

## End-to-end ingestion workflow

External material should be ingested through a composed workflow rather than one monolithic skill:

```text
ingest-source
    ↓
source-ingestion
    ↓
content-synthesis
    ↓
entity-resolution
    ↓
entity-enrichment
    ↓
note-routing
    ↓
note-conformance
    ↓
persist notes
```

The stages have distinct outputs:

1. `source-ingestion` creates or updates the normalized raw source note.
2. `content-synthesis` produces the standalone interpretive note.
3. `entity-resolution` identifies existing entities referenced by the note.
4. `entity-enrichment` adds meaningful relationships and creates missing entities where appropriate.
5. `note-routing` determines the destination and filename.
6. `note-conformance` validates the note against repository conventions.
7. The orchestrator persists the resulting notes and reports the outcome.

This decomposition allows:

- each stage to be tested independently;
- source retrieval to be retried without repeating interpretation;
- synthesis to be rerun without reacquiring the source;
- routing or schema changes to be applied independently;
- entity matching to improve without rewriting unrelated content;
- failures to be associated with a specific stage;
- the same underlying skills to support workflows other than ingestion.

---

## Query, research, and revision boundaries

These three skills should remain distinct.

```text
knowledge-query
```

Answers a question using knowledge already present in the KB.

```text
knowledge-research
```

Investigates a question using the KB and, where necessary, external evidence.

```text
note-revision
```

Integrates new knowledge into an existing durable note.

A typical research workflow may therefore be:

```text
knowledge-query
→ identify gaps
→ knowledge-research
→ note-revision or create new note
→ entity-enrichment
→ note-routing
→ note-conformance
```

### Source-specific ingestion skills

Examples:

```text
article-ingestion
meeting-transcript-ingestion
youtube-ingestion
kindle-ingestion
readwise-ingestion
social-post-ingestion
```

The central design principle is that user-facing workflows may compose multiple skills, while each underlying skill retains one clear and independently testable responsibility.

---

### Existing skill issues

Based on an analysis of skills in `~/Projects/kb/.hermes/skills`.

1. Fictional gbrain CLI surface (worst by raw count — ~20+ of 59 files)
Most ingestion, research, and meta skills invoke a non-MCP gbrain CLI (gbrain timeline-add, gbrain check-backlinks fix, gbrain files upload-raw, gbrain put/get) that doesn't exist — CLAUDE.md mandates the mcp__gbrain__* MCP tools instead, and only quickcapture-ingest actually follows that. This isn't ambiguity, it's agents literally running commands that fail. Same pattern shows up as dev-repo boilerplate copy-pasted wholesale into 14 skills (test/skills-conformance.test.ts, skills/manifest.json, skills/RESOLVER.md, src/core/check-resolvable.ts) — none of it exists in this vault.

2. Task-tracking system split-brain (worst by acuteness)
Three non-reconciled systems coexist: the canonical hermes kanban (per CLAUDE.md/AGENTS.md), the deprecated bd/beads workflow, and daily-task-manager's own ops/tasks.md page — which doesn't exist on disk. kb-commit's own linked reference doc still teaches bd create/bd claim and contradicts kb-commit's own top-of-file startup sequence in the same file. daily-task-prep still calls bd ready every run. This is the one skill (kb-commit) that's self-contradicting internally, and it's the highest in-degree node in the whole skill graph — nearly everything routes through it.

3. RESOLVER.md is internally self-contradictory, not just incomplete
Beyond the missing entity kinds you already found: RESOLVER.md's own "Linking" section lists a wikilink string as both a "Correct" and "Incorrect" example in the same file. Three separate canonical docs (_output-rules.md, SOUL.md, RESOLVER.md) disagree on markdown-link-vs-wikilink syntax for the exact same artifact. If the "single authority" file contradicts itself, that's a deeper problem than the filing-rule gap — it means there's no ground truth to reconcile against, even in principle.

4. Schema violations baked into the most-used skill templates
enrich and concept-synthesis's own page templates use title:/type: instead of name:, and omit most schema-required fields — this is the exact GBrain-entity-detection break schema.md warns about, and it's already bitten you once (you have a standing memory: "entity pages have no title field"). enrich is an exposed, constantly-invoked skill, so this template is actively teaching the wrong frontmatter shape every time it fires.

5. Phantom directories propagated across multiple files
originals/, personal/, deals/, media/x/ are referenced as write targets in voice-note-ingest, archive-crawler, signal-detector, brain-ops, and perplexity-research — none exist on disk. Following any of these literally creates orphaned, unrouted files outside the vault's real structure.

Lower-severity but real:

- Competing, non-cross-referencing standards for "what makes a good skill" (skill-creator vs skillify, 4-section check vs. 11-item checklist)
- Citation format drift (citation-fixer's own "fixed" example doesn't match quality.md's canonical format it claims to enforce)
- ~10 dangling related_skills references to skills that don't exist (himalaya, brain-publish, concept-diagrams, etc.)
- Directory-naming collisions that mirror your original complaint (note-taking/obsidian vs obsidian/readwise-plugin-mods — genuinely different skills, confusingly placed)

---

## Skill resolution

---

### Goals

- Allow users to override built-in skills without modifying system-managed files.
- Resolve skills deterministically from ordered local, user, and built-in sources, without merging implementations.
- Keep resolution transparent, validated, and resilient to upgrades and stale overrides.

The skill resolution system lets users customize built-in behavior by placing replacement skills in higher-precedence local directories, while preserving bundled skills as reliable fallbacks. It resolves each skill deterministically from an ordered set of sources, selects one complete skill directory without merging files across implementations, validates the chosen skill before use, and makes the active source easy to inspect. The design also supports upgrades safely by leaving user overrides untouched and surfacing when those overrides may have drifted from newer built-in versions.

The detailed requirements for that are specified in [the skill resolution specification](./skill-resolution-specification.md)
