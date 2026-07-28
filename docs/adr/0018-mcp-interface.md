---
title: MCP server interface, exposed as prepare/apply tool pairs
status: accepted
date: 2026-07-28
audience: wakil design
---

## Context

`wakil` has been CLI-only since Phase 1. `PROMPT.md`'s "Product Shape"
section states the CLI is the primary interface but is explicit that this
isn't exclusive: "The first version should not require a server, browser
UI, or remote runtime. However, the design should not prevent additional
interfaces later." An MCP (Model Context Protocol) server is such an
interface: it lets MCP clients (Claude Desktop, Claude Code, or any other
MCP-speaking agent) call into wakil's search, query, and ingest/enrich
flows directly, as typed tool calls, instead of shelling out to the CLI.

The risk this decision has to manage is collapsing wakil's existing
human-review checkpoints. ADR 0008 decomposed ingestion into a fixed,
code-sequenced DAG specifically to reject agent-decided sequencing ("a step
that must run, runs because code calls it, not because an agent
remembered to"), and `wakil ingest`/`wakil enrich` are two separate CLI
commands, each previewing a diff and requiring confirmation before writing.
A naive MCP design — one tool that runs capture through enrichment through
PR creation in a single call, with no intermediate response — would
reintroduce exactly the shape ADR 0008 rejected, just one level up: instead
of wakil's internal DAG being agent-decided, the *decision to run the whole
pipeline unattended* would be delegated to whatever drives the MCP client.
It would also drop the CLI's pre-write preview entirely, since MCP tool
calls are request/response with no interactive confirmation prompt, and it
would skip past the draft-PR signal `git_service.py` already uses to mark
"captured but not yet enrichment-reviewed" (docs/adr/0003).

## Decision

Expose wakil as an MCP server via a new `wakil mcp serve` subcommand
(`src/wakil/mcp/`), with the following shape:

- **Packaging**: a `mcp` sub-Typer on the existing CLI (`cli/main.py`), not
  a separate binary — one install path, consistent with every other
  command group (`ingest`, `git`, `memory`, `schema`, `skills`, `qmd`).
- **Transport and workspace binding**: stdio only, bound to exactly one
  workspace for the life of the process, resolved via the same
  `-w/--workspace` / `WAKIL_WORKSPACE` mechanism every other command uses.
  No per-call workspace parameter — this mirrors how a real MCP client
  config would point at "my personal KB," not a multi-tenant server.
- **Read tools** (`status`, `search`, `query`, `memory_list`, `memory_show`,
  `relationships`, `sources_list`, `sources_show`, `git_summary`,
  `git_history`, `skills_list`) are thin wrappers over the existing
  `app/*_service.py` functions, returning plain data instead of Rich
  output. No service-layer refactor was needed: the CLI is already
  "resolve workspace -> call a service function -> format with
  `wakil.ui.console`," and the service layer has no Rich/Typer dependency.
  Memory lifecycle transitions (`promote`/`reject`/`archive`) are
  deliberately **out of scope** for this pass — not silently bundled in.
- **Write tools are prepare/apply pairs**, one pair per DAG phase, mirroring
  `_run_ingest`/`enrich` in `cli/main.py` exactly, minus Rich output and
  `typer.confirm`: `ingest_prepare` / `ingest_apply`, then
  `enrich_prepare` / `enrich_apply`. `*_prepare` returns a preview (the same
  information the CLI's `print_capture_proposal`/`print_enrichment_proposal`
  show) and caches the underlying proposal object server-side, keyed by an
  id, in a new in-process `mcp/proposals.py` cache — needed because unlike
  the CLI (where prepare and apply happen moments apart in one process
  invocation), MCP prepare/apply are two separate tool calls that may
  happen in separate turns. `*_apply` looks up the cached proposal and
  performs the actual write, landing (branch, commit, draft-then-ready PR)
  exactly as `git_service.py` already does for the CLI. Every existing
  safety property is preserved: branch isolation, the draft->ready PR
  signal, commits scoped to exactly the files wakil wrote, and
  `abandon_landing` on any failure.
- The two-call boundary is the mechanism, not a policy about how eagerly a
  client should chain the two calls — that policy question, and why the
  default should be to chain quickly for routine cases, is a separate
  decision (docs/adr/0019).

## Consequences

- An MCP client can complete a full capture -> enrich -> PR cycle, but only
  by making four tool calls, never one — there is no tool that writes
  anything without first returning a preview from a separate call.
- `mcp/tools.py` duplicates a small amount of orchestration logic already
  in `cli/main.py`'s `_run_ingest`/`enrich` (the prepare-land-apply-land
  sequence) rather than sharing it directly with the CLI, since the CLI's
  own version is entangled with Rich output and `typer.confirm`. This is
  the one real drift risk from this change: a future fix to that sequence
  in the CLI (e.g. a new landing edge case) needs a matching fix in
  `mcp/tools.py`, and there is no test that would catch the two drifting
  apart other than each surface's own test suite passing.
- The proposal cache is in-memory only, with no cross-restart persistence.
  A server restart between `prepare` and `apply` loses the pending
  proposal; the client just calls `prepare` again. Acceptable for a
  single-user local tool; would need reconsidering for any future
  multi-process or long-idle-gap use case.
- Read tools give an MCP client the same search/query/memory visibility the
  CLI has, with no new review-mechanism questions — there's nothing to
  gate on a read.

## Sources

- `PROMPT.md`, "Product Shape" ("the design should not prevent additional
  interfaces later").
- `docs/adr/0008-ingestion-decomposition-reject-multi-agent-mechanism.md`
  ("a step that must run, runs because code calls it, not because an agent
  remembered to").
- `docs/adr/0003-git-native-change-tracking.md` (branch/commit/PR landing
  mechanism, draft-then-ready PR signal).
- `CLAUDE.md`, Working Agreement items 11-12 ("Show reviewable diffs for
  knowledge-base modifications." / "Do not silently rewrite user
  knowledge.").
- `src/wakil/cli/main.py`, `_run_ingest` (L400) and `enrich` (L511) — the
  CLI sequence `mcp/tools.py`'s write tools mirror.
