---
title: Decompose ingestion into classify/extract/resolve/enforce/finalize, reject the multi-agent mechanism
status: accepted
date: 2026-07-10
audience: wakil design
---

## Context

`docs/ingestion-model.md` works through a concrete worked example — a GBrain/Obsidian vault whose ingestion is organized as a dozen cooperating Claude Code "skills" that an LLM agent reads and follows, with one agent classifying input and delegating to other agents for type-specific extraction. The document's central claim is that ingestion decomposes into five separable concerns regardless of how each one is executed: **classify** the input, **extract** type-specific structure, **resolve entities** against the existing knowledge base (shared across all source types), **enforce invariants** that gate every write, and **finalize** (commit). The document argues this decomposition is what keeps ingestion code from rotting into an `if kind == ...` ladder as source types grow, and that the benefit comes from not conflating the five concerns — not from how each one is executed.

The document's "Where this meets wakil's principles" section addresses the worked example's *mechanism* directly: a router agent inferring content type and dispatching to other agents, each following its own markdown procedure, is exactly what `CLAUDE.md`'s Design Biases section lists under "Avoid" — "large agent frameworks," "multi-agent orchestration," and "overly abstract plugin architectures" — in favor of "explicit commands" and "small composable services." Importing that mechanism wholesale would violate wakil's stated design biases.

## Decision

Keep the five-concern decomposition (classify / extract / resolve entities / enforce invariants / finalize) but implement it as a fixed, code-sequenced DAG rather than agent-decided delegation:

- Classification stays an explicit CLI argument (`wakil ingest <kind> ...`), not content-inferred routing.
- Each extraction path pairs a mechanical function (fetch/parse/OCR, no model call) with a `wakil/skills/<kind>/SKILL.md` prose file (judgment only, no schema) that code loads and folds into a model call, validated against a shared `ExtractionOutput` Pydantic contract.
- Entity resolution is split out into its own always-invoked DAG node (not agent-remembered), producing `EntityResolution` results.
- Invariant enforcement (`validate_proposal()`) sits between `prepare_ingest`/`prepare_enrichment` and `apply_ingest`/`apply_enrichment` as one explicit step.
- Finalize is git-native (`--commit`/`--branch`/`--pr`), already implemented in `app/git_service.py`.

This captures the worked example's real asset — accumulated, editable judgment per source kind — without adopting agent-decided control flow: a step that must run, runs because code calls it, not because an agent remembered to.

## Consequences

- New source kinds (PDF, tweet, webhook capture) add a mechanical extractor and a prose skill file, not a longer `if/elif` branch or a bloated shared prompt.
- Entity-resolution bugs get fixed in one place instead of being re-implemented per source kind.
- The DAG's topology is never agent-decided, avoiding the class of failure the worked example's own skill docs guard against with repeated anti-pattern warnings (e.g. "a meeting is NOT fully ingested until enrich runs for every entity").
- wakil does not get an "adjacent maintenance" analogue (backlink repair, stale-page detection) yet; that remains a deliberately separate, lower-frequency command to build later.

## Implementation

- **PR #9** — "enrichment DAG — skills, contracts, entity resolution, validate_proposal (Phase C)" — opened 2026-07-12, merged 2026-07-15.
- **PR #13** — "Wire the skill resolver into wakil enrich's DAG" — opened 2026-07-16, merged 2026-07-16.
- **PR #14** — "Entity-revision DAG, schema page-shapes, and QMD workspace collections" — opened 2026-07-18, merged 2026-07-18.

## Sources

- `docs/ingestion-model.md`, sections "The general pattern, extracted" and "Where this meets wakil's principles"
- `CLAUDE.md`, "Design Biases -> Avoid" (lists "large agent frameworks," "multi-agent orchestration," "overly abstract plugin architectures")
