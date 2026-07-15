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

This is the set of core skills that the `wakil` kb should support.  The key functionality should be captured from `~/Projects/kb/.hermes/skills`, after refactoring and fixing of the issues outlined below.  The Hermes skills contain a lot of useful nuance after having evolved and this should not be lost, just normalized and abstracted away from `gbrain`.

- Ingestion skill
  - Ingest meeting transcripts, articles from a URL, articles from a reader app (e.g. Readwise), articles or books from Kindle, etc., YouTube videos, Twitter/X links.
  - Store the source material as the raw content in a structured way
  - Conduct an interpretive synthesis into a standalone note in the KB
  - Enrich that standalone note by link it to existing (or new) entities.
- A routing, filing, annotation skills based on `./RESOLVER.md` and `./SCHEMA.md`
- A research skill
- A query skill that wraps QMD
- pr-create & kb-commit skills that normalizes git interactions in a kb-specific way
- A skill to create new or override existing skills (e.g. by Hermes)

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
