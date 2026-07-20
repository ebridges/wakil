---
title: If Storage Moves to a Server Database (Supabase or Otherwise)
status: draft
audience: wakil design
---

# If Storage Moves to a Server Database (Supabase or Otherwise)

This is a follow-up to the concurrent-ingest work in `git_service.py`,
`config/settings.py`, and `storage/database.py`: per-source branch/PR
tracking, workspace identity resolved across git worktrees via the shared
`.git` common-dir, and SQLite tuned for concurrent writers (`WAL` +
`busy_timeout`). That work assumes one SQLite file per workspace, colocated
with the git checkout. This document is *not* a proposal to change that —
it's the answer to "what would actually have to change if we did," written
down now while the reasoning behind the current design is fresh, so a
future decision to move isn't made by someone re-deriving all of this from
scratch.

## Read this first: is this even the right move?

`CLAUDE.md`'s Prime Directive applies here as much as anywhere: **does this
clearly improve local Markdown knowledge work for one user?** A server
database is not an upgrade to the current design — it's a scope change,
and it cuts directly against several of wakil's stated design biases
("avoid remote runtimes," "avoid hosted product assumptions," "local-first
CLI focus"). SQLite-on-disk is the *correct* choice for the scope wakil
targets today (one user, one or several machines' worktrees, everything
git-native and inspectable as files). Don't read the rest of this document
as a queue of work to schedule — read it as the answer to "if the scope
genuinely changes, here's what changes with it."

**The concrete triggers that would justify this**, none of which are true
today:

- **Multi-device sync as a real requirement** — the same knowledge base
  actively worked from two or more machines that aren't sharing a
  filesystem (today's git-worktree answer only works within one machine;
  git push/pull already syncs the *Markdown*, but not `wakil.db`'s
  memories/sources/FTS index across machines).
- **Concurrency past what local worktrees can express** — the current fix
  handles "a handful of worktrees on one machine." A queue of dozens of
  agent-driven ingests (Plan 3's agent API, at scale) run from multiple
  machines or a hosted runner is a different regime.
- **A genuine second user** — today's data model has no concept of
  per-user access control; it assumes one person. If that stops being
  true, storage has to change regardless of where the file lives.

Absent one of these, the honest recommendation is: don't. If one of them
does become true, everything below is the map.

## What doesn't change

Worth stating plainly, because it's easy to assume a storage-layer swap is
bigger than it is: the **git side is untouched**. `git_service.py`'s
per-source branch resolution, draft-then-ready PR lifecycle, and
`integrations/git.py`/`integrations/github.py`'s subprocess wrappers don't
know or care where `wakil.db` lives — they operate on the local git
checkout regardless. Everything in the app layer (`ingest_service.py`,
`search_service.py`, `git_service.py`, ...) is already written against a
SQLAlchemy `Session`, not raw SQLite — that abstraction boundary was
already correctly drawn, and a backend swap is a `storage/` and
`config/settings.py` problem, not an app-layer rewrite.

## What changes, mechanism by mechanism

### 1. The concurrency problem this document's parent work solved goes away — differently

`PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=30000`
(`storage/database.py`) exist because SQLite has exactly one writer at a
time for the whole file. Postgres doesn't have that constraint — MVCC
gives every transaction its own consistent snapshot, and writers only
block each other when they touch the *same rows*, not the whole database.
The busy-timeout retry-on-lock-contention problem this fix addresses
mostly stops being a problem at all.

What replaces it isn't "nothing" — it's a different, smaller set of
concerns:

- `statement_timeout` / `lock_timeout` (Postgres session GUCs, set via
  `SET` on connect or in the connection string) are the direct analogues
  of `busy_timeout` — a bound on how long a session will wait for a lock
  before giving up, for the rarer case where two writers *do* touch the
  same row (e.g. two processes racing to update the same `Source`).
- The principle this work already verified — **never hold a transaction
  open across slow work** (an LLM call, a network fetch) — matters *more*
  with a shared server, not less: a long-held transaction on a server
  blocks other clients' access to the same rows and prevents autovacuum
  from reclaiming dead tuples. `prepare_*`/`apply_*`'s existing shape
  (read session closes before any model call; write session opens only at
  the moment of writing) is already correct for this and needs no change
  — it was verified for exactly this reason during the SQLite work.
- Connection *pooling* becomes a real concern in a way it isn't for
  SQLite. Each `wakil` CLI invocation opening a fresh Postgres connection
  is fine for occasional use but wasteful under real concurrency (Plan 3's
  agent API, called frequently); Supabase's built-in pooler (PgBouncer, in
  transaction mode) or `sqlalchemy`'s own pool tuning would be the answer,
  not something to build from scratch.

### 2. Workspace identity: the git-worktree hack becomes unnecessary — replace it, don't port it

`WorkspaceConfig.state_root` (resolved via `git.worktree_anchors`'
common-dir) exists to answer one question: "which physical `.wakil/`
directory does this checkout's data live in?" That question only exists
*because* the database is a local file that has to live somewhere findable
relative to a git checkout. A server database doesn't have that problem —
a connection string doesn't care which worktree you're standing in.

**Don't port `state_root` resolution to the server case.** Replace it with
an explicit workspace identifier:

- Add a `workspace_id: uuid` (or slug) to `.wakil/config.yaml` — small,
  non-sensitive, and safe to either keep gitignored (matching today) or
  **commit to the repo**, which is arguably the better call here
  specifically: a committed workspace id means `git worktree add` (or a
  fresh `git clone` on a second machine) just works with zero extra setup
  — every checkout already knows which server-side workspace it belongs
  to, without needing `.git`-common-dir gymnastics or a manual `wakil
  init` step per worktree. This is a case where the git-native answer is
  simpler than the one this document's parent work had to build for the
  local-file case.
- `_ensure_workspace`/the seven `Workspace.root_path == str(config.state_root)`
  lookup sites (`workspace_service.py`, `search_service.py`,
  `query_service.py`, `git_service.py`, `ingest_service.py` ×2,
  `schema_migrate_service.py`) all collapse to one thing: look up (or
  create, once, at `wakil init` time — not implicitly on every command)
  the `Workspace` row by that id. Simpler than what exists today, not more
  complex.
- `is_initialized`/`find_workspace_root` stop needing the git-worktree
  fast-path/slow-path split (`config/settings.py`) — "initialized" becomes
  "does `.wakil/config.yaml` exist and carry a `workspace_id`," full stop,
  regardless of which worktree you're in.

### 3. Migrations: upgrade-on-open becomes actively dangerous

`storage/database.py`'s `init_db` currently checks-and-upgrades on *every*
`open_session` call, against a local file only one process is realistically
touching at that instant. Against a **shared server**, several concurrent
`wakil` processes each independently deciding "I should run `alembic
upgrade head`" is a real hazard — races between concurrent schema changes,
or a client on an older wakil version trying to "fix" a database a newer
version already migrated.

This needs to become an explicit, singular operation — a `wakil db
migrate` command (or a deploy-time step, if this is ever run as a hosted
service) that a human or CI runs deliberately, with client `open_session`
calls simply checking the server is *at* the expected revision and failing
loudly (not attempting to fix it) if not.

### 4. Full-text search: FTS5 has no Postgres equivalent

`storage/fts.py` is SQLite-specific — `CREATE VIRTUAL TABLE ... USING
fts5(...)` plus triggers keeping it in sync with `notes`/`memories`/
`sources`. There's no drop-in Postgres equivalent; the two real options:

- Postgres native full-text search — `tsvector` generated columns +
  `GIN` indexes, `to_tsvector`/`plainto_tsquery`. Structurally similar
  (still trigger- or generated-column-synced), but different syntax and
  ranking behavior (`ts_rank` vs. SQLite FTS5's bm25) — `search_service.py`
  callers would need re-tuning, not just a backend swap.
- Lean on QMD more and Postgres FTS less — QMD is already a parallel,
  independent search backend (`integrations/qmd.py`); if it already covers
  the cases that matter, native Postgres FTS could be scoped down to a
  simpler `ILIKE`/`pg_trgm` fallback rather than reproducing FTS5's
  feature set.

Either way, this is real, scoped work — size it separately, don't assume
it rides along for free with the storage swap.

### 5. Credentials: never on disk, same pattern already established

`llm/client.py` already sets the precedent: provider credentials come from
`os.environ` (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, ...), never written to
`.wakil/config.yaml` or anywhere else on disk. A Supabase/Postgres
connection string follows the identical pattern — `WAKIL_DATABASE_URL` (or
Supabase's own env var conventions) read at connect time, never persisted.
`.wakil/config.yaml` keeps carrying non-sensitive identity
(`workspace_id`, `name`) only, exactly as it does today.

### 6. The dedup race this work flagged has since been closed — the fix carries over as-is

`TODO.md` originally flagged `Source.content_hash` dedup's check-then-insert
race (no unique constraint, so two concurrent identical-content ingests
could both create a `Source` row instead of one being caught as a
duplicate) — confirming, as noted at the time, that this **wasn't a reason
to wait for a server database**: `uq_sources_workspace_content_hash`
(migration 0004) plus `apply_capture` catching the resulting
`IntegrityError` closed it directly in SQLite, no Postgres required. That
fix carries over unchanged to a server backend — a `UNIQUE(workspace_id,
content_hash)` constraint and `ON CONFLICT`/`IntegrityError` handling work
the same way in Postgres; nothing here is SQLite-specific enough to need
redoing.

### 7. Multi-workspace tenancy, if it comes up

Today one SQLite file is one workspace, full stop — filesystem isolation
is the isolation. A shared Postgres/Supabase instance could plausibly host
several workspaces (several people, or one person's several knowledge
bases) in one database. The good news: every table already carries
`workspace_id` and every query already filters by it — that pattern was
already necessary for the SQLite case (one workspace's tables, filtered by
its own id) and translates directly. What's new is that a *bug* filtering
by the wrong id now leaks across workspaces instead of just being wrong
within one file — worth a deliberate pass adding Postgres row-level
security policies (Supabase's own recommended pattern) as defense in depth
if multi-tenancy is real, rather than relying on application-level
filtering alone.

## Staged migration path, if/when this happens

Roughly in dependency order — each stage is independently useful and
testable, not a big-bang rewrite:

1. **Explicit workspace identity** (§2) — lands first regardless of
   backend, since it's a strict simplification over the current
   git-worktree-derived `state_root`. Ship it against SQLite first,
   prove it, *then* swap the backend underneath it.
2. **Explicit migration command** (§3) — also backend-agnostic groundwork;
   stop relying on implicit upgrade-on-open before there's a shared server
   for that behavior to endanger.
3. **`create_db_engine`/`storage/database.py` grows a Postgres branch** —
   connection-string-driven, pooled, with the WAL/busy_timeout PRAGMA path
   guarded behind "only if SQLite" (the `_set_sqlite_pragmas` listener
   already checks `dbapi_connection`'s module name for exactly this kind
   of branching — extend that pattern, don't replace it).
4. **FTS backend decision** (§4) — sized and scheduled on its own once the
   above lands, not bundled in.
5. **RLS / tenancy hardening** (§7) — only if multi-workspace-per-server is
   actually happening.

Each stage should ship with the same discipline as the concurrent-ingest
work this document follows up on: a real empirical test against the
target backend (not just unit tests against mocks), verifying the actual
failure modes this document predicts rather than assuming the migration
guide was right.
