---
title: One advisory lock per checkout, not per-session worktree isolation
status: accepted
date: 2026-08-07
audience: wakil design
---

# 0021-advisory-lock-per-checkout-not-worktree-isolation

## Context

`wakil ingest`/`wakil enrich` own the git working tree for the length of a
command: `prepare_landing` switches onto the source's branch, files are
written, `land_ingestion` commits and returns. Between those points sit a
model call and (without `--yes`) a human confirmation, so the window is
seconds to minutes. Nothing prevented a second wakil process from running the
same sequence in the same checkout at the same time.

Issue #182 reports what that produces, corroborated across three sessions:
uncommitted edits silently replaced with a different session's content;
`git reflog` showing checkouts the operator never issued; the same source
captured twice onto two branches eight minutes apart; and — the detail that
identifies the second actor — two orphaned `wakil mcp serve` pairs already
running against the workspace before the session started, doing their own
enrichment writes and commits. Killing them stopped the interference. So the
concurrent process is not necessarily another human's session; a leftover MCP
server from an earlier session is enough on its own.

This compounds with #181: because `land_ingestion` never verified HEAD before
committing, a checkout moved by the other process meant the commit landed on
whatever branch HEAD had drifted to — `main`, in the reported case — while the
output claimed the ingest branch.

`docs/TROUBLESHOOTING.md`'s "Git worktrees fix the ingest lock race, but
wakil's workspace identity isn't worktree-aware" entry already investigated
the neighbouring question during PR #15, and found that separate worktrees do
eliminate `.git/index.lock` contention — but that wakil's `Workspace` row was
keyed on `root_path`, so a second worktree silently created a second,
disconnected workspace. `WorkspaceConfig.state_root` (`config/settings.py`)
was introduced to fix exactly that.

## Decision

An advisory `fcntl.flock` held around the whole
checkout → write → commit → return sequence, acquired at the CLI and MCP
boundary. Not per-session worktree isolation.

- **Scope is the command, not the git call.** `cli/main.py` wraps
  `_run_ingest` and `enrich`; `mcp/tools.py` wraps `ingest_apply`,
  `enrich_prepare`, and `enrich_apply`. Locking inside `git_service`
  functions would leave the file-writing window between them unprotected.
  `--local` performs no git operations and does not contend.
- **The key is `root_path`, not `state_root`.** The protected resource is a
  git index and working tree, and every linked worktree has its own. Keying
  on `state_root` — which worktrees deliberately share so they resolve to one
  `Workspace` row — would serialize checkouts that are already safe in
  parallel, defeating the mitigation TROUBLESHOOTING.md recommends.
- **`flock`, not an `O_EXCL` PID file.** The kernel releases a `flock` when
  the holder exits, including on `SIGKILL` and on a crash. There is therefore
  no stale-lock problem: no TTL, no liveness probe, no reaper, and no way to
  wedge a workspace permanently by killing wakil at the wrong moment. The JSON
  in the lock file decorates the error message and is never consulted to
  decide whether the lock is held.
- **Fail fast by default**, naming the holder's pid and argv and pointing at
  the orphaned-`mcp serve` check that actually fixed it for the reporter.
  `WAKIL_GIT_LOCK_TIMEOUT=<seconds>` opts into bounded waiting.
- **MCP does not hold the lock across `prepare` → `apply`.** Those are
  separate tool calls with a cross-turn gap, a 1-hour `ProposalCache` TTL
  (`mcp/proposals.py`), and no guarantee `apply` is ever called. Holding
  across them would wedge the workspace on every declined or abandoned
  proposal. Instead `enrich_prepare` releases the lock and returns the
  checkout to the default branch before handing control back, caching only the
  proposal; `enrich_apply` re-acquires and re-runs `prepare_landing`, which is
  already idempotent because it resumes `Source.git_branch`.
- **Every write path takes it, not just ingest/enrich.** `schema migrate
  --commit`, `entities compile --commit`, and `sources backfill-abstract`
  also rewrite files in the working tree; leaving them out would have left the
  #182 failure shape wide open (terminal B rewriting frontmatter across the
  vault while terminal A has the tree parked on an ingest branch, landing the
  migration inside someone else's PR). The lock covers the *write*, not just
  the commit, since an unlocked write lands on whatever branch the other
  process parked.

  Note the limit precisely: the lock guarantees no *other wakil process* is
  mid-write, not that the branch is the one the operator expected. These
  commands are branch-agnostic by design — they commit on the current branch
  — so if a human left the tree on an ingest branch, they still land there.
  Only `land_ingestion` asserts a specific branch (#181's
  `_assert_on_branch`), because only it has an intended branch to assert.
- **Deliberately out of scope:** cross-machine or network locking, locking
  read-only commands (`search`, `query`, `status`, `sources list`), and any
  attempt to make two processes *cooperate* rather than take turns.

## Alternatives considered

- **Give each session its own `git worktree` automatically.** Rejected for
  now. It is the more thorough fix and the one #182 lists first, but
  TROUBLESHOOTING.md records that worktrees trade a loud, retryable failure
  (lock contention) for a silent, worse one (fragmented workspace identity)
  until workspace rows are anchored on something stable — issue #168, still
  open. Worth revisiting once #168 lands; it is additive to this decision, not
  a replacement, since a lock is still wanted within a single worktree.
- **Rely on the HEAD assertion alone** (#181's `_assert_on_branch`). Rejected
  as insufficient: it converts silent corruption into a loud failure at commit
  time, which is a real improvement, but it does nothing for the working-tree
  clobbering #182 reports — an entire enrichment's model work can still be
  lost to another process overwriting files before the commit is reached. The
  two are complementary: the lock prevents the drift, the assertion catches it
  if prevention is bypassed (an unlocked path, a user `git switch`).
- **Have wakil shut down its own orphaned `mcp serve` processes.** Rejected as
  out of scope and unsafe to generalize — wakil cannot tell a stale server
  from a live one belonging to a different client.

## Consequences

- Two wakil commands against one checkout now serialize. For a single-user
  local tool this is the intended behaviour, but a caller that previously
  "got away with" overlapping invocations will now see a hard failure. This is
  the point: the alternative was silent data loss.
- The failure message is the main user-facing surface, so it names the pid,
  the argv, the worktree escape hatch, and the `WAKIL_GIT_LOCK_TIMEOUT`
  override. A bare "workspace is locked" would have left the reporter exactly
  as stuck as before.
- **Known gap:** the lock only guards processes that take it. A hand-run
  `git checkout` in another terminal, or any wakil code path added later that
  drives git without acquiring it, still moves HEAD underneath a running
  command. #181's HEAD assertion is what makes that case loud rather than
  silent, which is why both landed together rather than either alone.
- **The lock is not reentrant**, because `flock` is per open file description
  rather than per process. Two concurrent write tool calls in one
  `wakil mcp serve` process (the MCP SDK dispatches sync tools on worker
  threads) will deny each other, and the busy message detects that case by pid
  and says so rather than blaming a leftover server. Take the lock once, at
  the command boundary.
- **A proposal is held, not consumed, until the write actually begins.**
  `ingest_apply`/`enrich_apply` `peek` rather than `pop`, and `claim` only once
  past the point of no return. Consuming first meant a *transient* failure — a
  contended lock, a tree the human dirtied during review — destroyed an
  enrichment proposal worth two model calls while advising a retry the client
  could no longer perform. `claim` raises when the id is already gone, which
  is what preserves single-use: two worker threads can both `peek` the same id
  while queued on the lock, and only one may go on to apply it.
- **Known gap:** `enrich_prepare` and `enrich_apply` release between calls, so
  the branch can change in the interval. `apply_enrichment`'s existing
  stale-file guard (comparing `update.old_content` against a fresh disk read)
  is what covers this, and it predates this ADR. The alternative — holding
  across the gap — was rejected above as strictly worse.
- POSIX-only. `fcntl` has no Windows equivalent, and no shim was added: wakil
  is a local-first macOS/Linux CLI with no Windows support to preserve.
