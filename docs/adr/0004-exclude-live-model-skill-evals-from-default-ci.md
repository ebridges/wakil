---
title: Exclude Live-Model Skill Evals from the Default CI Gate
status: accepted
audience: wakil design
---

# Exclude Live-Model Skill Evals from the Default CI Gate

## Context

`wakil` has skill-catalog evals that make real calls to a live model provider
to check skill-selection/behavior quality. These evals are valuable signal,
but they differ from ordinary unit/integration tests in ways that matter for
CI design:

- They require a live `ANTHROPIC_API_KEY` secret and make real network calls
  to a hosted model endpoint, which costs money and is subject to provider
  latency/availability.
- Their outcomes are less deterministic than code-level tests, since they
  depend on model behavior rather than pure application logic.
- CLAUDE.md's stated philosophy for this project is to keep things simple,
  avoid hidden background behavior, and keep model access provider-abstracted
  but minimal — a mandatory, secret-consuming, non-deterministic call to a
  live model on every push/PR runs against that grain.

Blocking the default `push`/`pull_request` CI gate on live-model evals would
make every contribution's merge status depend on external API availability,
cost, and model non-determinism, none of which reflect the correctness of the
code change under review.

## Decision

Live-model skill evals are excluded from the default CI gate and run only on
demand:

- pytest evals are tagged with a dedicated `eval` marker
  (`markers = ["eval: live-model skill evaluations (real API calls; excluded
  by default, run with -m eval)"]` in `pyproject.toml`), and the default
  pytest run deselects them via `addopts = "-m 'not eval'"`.
- The default CI workflow (`.github/workflows/ci.yml`) runs `uv run pytest -q`
  on `push`/`pull_request`, which — via the marker exclusion — never executes
  the eval-marked tests, and requires no model API key.
- A separate workflow (`.github/workflows/skill-evals.yml`) runs the
  live-model evals (`uv run pytest -m eval -q`, with `ANTHROPIC_API_KEY`
  injected from secrets) but is triggered only by `workflow_dispatch`, i.e.
  opt-in/manual, never automatically on push or PR.

## Consequences

- The default CI gate (lint + test) stays fast, deterministic, and free of
  external dependencies; merges are never blocked by model-provider outages,
  cost, or nondeterministic eval flakiness.
- Live-model skill evals remain available as a first-class, explicit check —
  they are not deleted or ignored, just decoupled from the default gate — and
  can be run manually via `workflow_dispatch` when a contributor wants that
  signal.
- Because evals are opt-in, a regression in skill-selection quality will not
  automatically fail CI; running the evals (or reviewing their results) has
  to be a deliberate, separate action rather than something the default gate
  enforces.
- Adding new eval-marked tests requires no changes to the default CI
  configuration — they are excluded by construction via the marker, keeping
  the two test populations (deterministic unit/integration vs. live-model
  eval) cleanly separated by a single pytest marker rather than by file
  location or naming convention alone.

## Sources

- `/Users/ebridges/Projects/wakil/pyproject.toml` (`[tool.pytest.ini_options]`: `markers = ["eval: live-model skill evaluations (real API calls; excluded by default, run with -m eval)"]`, `addopts = "-m 'not eval'"`)
- `/Users/ebridges/Projects/wakil/.github/workflows/ci.yml` (default `push`/`pull_request` gate: `uv run pytest -q`, no eval marker, no model API key)
- `/Users/ebridges/Projects/wakil/.github/workflows/skill-evals.yml` (`on: workflow_dispatch: {}`; `uv run pytest -m eval -q` with `ANTHROPIC_API_KEY` from secrets)
- Commit `80da1e9` "ci(skills): wire up opt-in live-model skill evals"
- Session transcript `/Users/ebridges/.claude/projects/-Users-ebridges-Projects-wakil/3e0a3930-d35f-4ba1-9f24-61e06cf3fde2.jsonl`: "CLAUDE.md's philosophy (simple, no hidden behavior, provider-abstracted but minimal) strongly implies live-model evals shouldn't block the default CI gate."
