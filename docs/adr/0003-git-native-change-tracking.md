---
title: Git-Native Change Tracking (Branches/PRs for Ingest)
status: accepted
date: 2026-07-09
audience: wakil design
---

## Context

`wakil`'s product thesis commits to being a "git-native knowledge-work
agent" (`PROMPT.md`), and its build plan names git-native change tracking
explicitly as Phase 4: "make knowledge-base edits safe and reviewable,"
with the success criterion "wakil can perform a meaningful ingest or note
update on a branch with a clear commit" (`PROMPT.md`, "Phase 4: Git-Native
Changes"). The top-level use-case list in `PROMPT.md` states the same
intent twice: "Use git history to understand how the knowledge base has
evolved" and "Use pull requests or branches for larger ingests and
modifications."

Before Phase 4, `wakil ingest` wrote Markdown files and database rows
directly with no git integration — there was no branch isolation, no
commit convention, and no reviewable diff boundary between "wakil proposed
this" and "the user's own in-progress edits."

## Decision

Make git branches, scoped commits, and (optionally) GitHub pull requests
the mechanism by which `wakil` lands knowledge-base changes, instead of
writing directly to the user's working tree/branch.

Concretely, per PR #4 ("feat(phase-4): git-native ingest — branches,
commit conventions, gh PRs, git summary", merged 2026-07-09):

- A checked-write layer in `integrations/git.py` (branch creation, commit
  staged to *exactly* the paths wakil wrote, push, file history, wakil-branch
  listing) sits alongside the pre-existing never-crash read helpers, and a
  thin `integrations/github.py` wrapper shells out to the `gh` CLI for PR
  creation rather than integrating the GitHub API directly.
- `wakil ingest` gained `--commit`/`-c` (commit on the current branch,
  staging only wakil-written files — the user's own uncommitted work is
  never swept in), `--branch`/`-b` (require a clean tree, then create
  `wakil/ingest/<date>-<slug>`, deduped, before any files are written, and
  commit there), and `--pr` (implies `--branch`; pushes and opens a PR via
  `gh` with a summary and review checklist; degrades cleanly — clear error,
  not a crash — when `gh` or an `origin` remote is absent).
- Commit messages follow a fixed convention (`wakil ingest: add <title>`,
  summary as body), and every commit is recorded in `git_changes` and
  back-filled onto the `IngestRun`.
- New read commands, `wakil git summary` and `wakil git history <path>`,
  surface current branch, pending changes, recent commits, and per-file
  history without leaving the CLI.

The commit and PR body itself frames this as satisfying the Phase 4 success
criterion directly ("Success criterion met — 'wakil can perform a
meaningful ingest or note update on a branch with a clear commit.'"), and
`docs/ingestion-model.md`'s later cross-cutting review of the ingestion
pipeline treats this piece as already settled: "Finalize is already
handled, and handled more rigorous[ly] ... `--commit` / `--branch` / `--pr`
in `app/git_service.py` and `integrations/git.py` already give wakil a
reviewable, git-native finalize step — closer to a proper code-review flow
than the worked example's plain commit. No change needed."

I could not find a document or transcript that lays out *why* git
branches/commits/PRs were chosen over an alternative review mechanism
(e.g., an in-app diff/approval queue, a database-only staged-changes table,
or writing straight to the working tree with `git diff` left for the user
to review manually). The choice appears to follow directly from the
project's stated thesis and design biases — "git-native," "human review,"
"show reviewable diffs for knowledge-base modifications" — rather than from
a recorded comparison of alternatives. This ADR records the decision and
its behavior faithfully; it does not invent a comparative rationale that
isn't in the source material.

## Consequences

- Every wakil-driven knowledge-base change is isolated on its own branch
  and commit (or, with `--pr`, its own pull request) rather than mixed
  into the user's current working-tree state, satisfying the working
  agreement's "show reviewable diffs for knowledge-base modifications" and
  "do not silently rewrite user knowledge."
- Staging is scoped to exactly the files wakil wrote, not `git add -A`, so
  the user's own uncommitted work is structurally protected from being
  swept into a wakil commit — this is explicitly covered by tests per the
  PR body ("tested explicitly").
- The `--pr` path is a soft dependency on the `gh` CLI and a configured
  `origin` remote; both integrations are written to degrade with a clear
  error rather than a crash when absent, consistent with keeping GitHub
  integration optional rather than load-bearing.
- This is a live decision, not a one-time landing: a subsequent in-flight
  change (PR #15, open as of 2026-07-20, "Default-on branch/commit/PR
  landing per source") extends the same mechanism — making branch/commit/PR
  the default behavior of `wakil ingest`/`wakil enrich` rather than opt-in
  flags, and tracking **one branch and one PR per source** across its whole
  capture-then-enrich lifecycle instead of one per command invocation.
  Related refinements captured in that work's session transcript
  (`.claude/projects/-Users-ebridges-Projects-wakil--claude-worktrees-git-integration/8c34f4e5-01ed-4041-8fdd-b1bb7953e8c7.jsonl`):
  branches must fork from the repository's actual resolved default branch
  rather than whatever happens to be checked out (a latent bug otherwise:
  "an ingest kicked off mid-review would silently stack on an unrelated
  branch"), and phase landings are recorded as PR **comments** rather than
  PR body rewrites, "since GitHub doesn't auto-update PR descriptions on
  new commits and rewriting the body risks clobbering manual edits." These
  are documented here as evidence of the decision's continued direction,
  not as part of the original Phase 4 decision itself.
- Large diffs produced by an ingest still need to be split into logical,
  reviewable commits per the repo's stated working agreement ("keep
  changes small," "show reviewable diffs") — the git-native mechanism
  provides the review boundary, but does not by itself guarantee any given
  commit stays small; that remains a per-change judgment call.

## Sources

- `PROMPT.md`, "Phase 4: Git-Native Changes" (goal, build list, and success
  criterion: "wakil can perform a meaningful ingest or note update on a
  branch with a clear commit").
- `PROMPT.md`, core use-case list, items 10-11 ("Use git history to
  understand how the knowledge base has evolved." / "Use pull requests or
  branches for larger ingests and modifications.").
- PR #4, "✨ feat(phase-4): git-native ingest — branches, commit
  conventions, gh PRs, git summary" (merged 2026-07-09T20:42:35Z,
  `https://github.com/ebridges/wakil/pull/4`).
- Commit `cc3e11534be76ec05312e31dbb83cabe30b2b777`, "✨ feat(git): add
  write operations to git wrapper and thin gh integration".
- Commit `1ba6f3a079a182090ae34962f0577a023d10f5d6`, "✨ feat(git):
  branch/commit/PR flags for ingest and git summary commands".
- `docs/ingestion-model.md`, section "Proposal: a concrete model for
  wakil" ("Finalize is already handled, and handled more rigorously.").
- `TODO.md`, "Phase 4: Git-Native Changes" checklist.
- PR #15, "Default-on branch/commit/PR landing per source" (open as of
  2026-07-20, `https://github.com/ebridges/wakil/pull/15`) — follow-on
  evolution, not part of the original decision.
- Transcript
  `~/.claude/projects/-Users-ebridges-Projects-wakil--claude-worktrees-git-integration/8c34f4e5-01ed-4041-8fdd-b1bb7953e8c7.jsonl`:
  "one branch and one PR per source** across its whole lifecycle
  (capture → enrich, possibly across separate sessions or agents), rather
  than the two disconnected PRs today's flag-based flow would produce if
  both steps used `--pr`."; "PR **comments** (not body rewrites) record
  each phase landing, since GitHub doesn't auto-update PR descriptions on
  new commits and rewriting the body risks clobbering manual edits.";
  "Branches now fork from the repo's actual default branch, not whatever
  happens to be checked out — was a latent bug where an ingest kicked off
  mid-review would silently stack on an unrelated branch."
- Transcript
  `~/.claude/projects/-Users-ebridges-Projects-wakil/544fe3a7-bef8-42b0-aba3-7ff553cb7a3f.jsonl`:
  "Per this repo's working agreement (\"keep changes small,\" \"show
  reviewable diffs\"), these need to land as separate commits rather than
  one bulk commit."
