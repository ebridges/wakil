# Wakil

[![CI](https://github.com/ebridges/wakil/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ebridges/wakil/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/ebridges/wakil)](https://github.com/ebridges/wakil/releases/latest)
[![License](https://img.shields.io/github/license/ebridges/wakil)](LICENSE.md)

> [!NOTE]
> **wakīl** /wa-KĪL/
> *noun* · Arabic **وكيل**
>
> 1. **An entrusted agent;** one authorized to act on another’s behalf.
> 2. **A representative, deputy, or proxy** whose role rests not merely on action, but on conferred trust.
> 3. **One who carries responsibility for another’s interests**, standing in the space between delegation and reliance.
>
> *From Arabic roots associated with entrusting, relying upon, and placing confidence in another.*

`wakil` is a local-first Python CLI agent for working with a personal Markdown
knowledge base (GBrain / Obsidian style): ingest, search, connect, revise, and
reason over Markdown notes. Markdown is the source of truth; SQLite is the
operational index; git provides history and review. Design decisions are
recorded as ADRs under `docs/adr/`. `wakil` doesn't require a specific
knowledge-base layout, but understands one when present — see
`docs/knowledge-base-conventions.md` for the directory/file conventions it's
designed around (`RESOLVER.md` for routing, entity schemas for page shape).

## Why wakil?

You keep notes, meeting transcripts, and articles in a folder of Markdown
files, and you want that folder to actually get smarter over time — without
handing it to a black box that silently rewrites your knowledge or requires
a hosted service. `wakil` is for that gap. Concretely:

- **"I just got out of a meeting and don't want to write it up."**
  `wakil ingest transcript ./raw/meeting.txt --context "Weekly product/engineering sync"`
  captures the raw transcript, then `wakil enrich <id>` summarizes it,
  extracts candidate memories and relationships, links it to people/company
  pages it mentions, and proposes a durable meeting note — all as a reviewable
  diff, never a silent rewrite.
- **"What did we actually decide about X?"**
  `wakil query "What did we decide about the priority of the changes to planset review?"` searches
  your notes, memories, and sources, and answers with citations back to the
  specific note or source — if the knowledge base doesn't support an answer,
  it says so instead of guessing.
- **"I don't want every AI-proposed fact to just merge into my notes."**
  Ingested facts land as `candidate` memories first. `wakil memory list
  --state candidate` reviews them; `promote`/`reject`/`archive` decide their
  fate. Nothing becomes durable, query-grounding truth without you deciding.
- **"I want to see (and revert) exactly what an agent changed."**
  Every ingest and enrichment lands on a `wakil/ingest/<date>-<slug>` branch,
  commits with a clear convention (`🧠 wakil ingest: ...`), and can open a
  pull request (`--pr`) — a real git diff is always the review surface, not
  a trust-me summary.
- **"I want to capture on the go, between meetings, without reviewing every
  field."** Point an MCP-speaking agent (Claude Desktop, Claude Code, a
  Hermes agent) at `wakil mcp serve` and have it follow the
  [`mcp-coordinator`](#mcp-server) skill: it captures and enriches in one
  chained pass, pausing only for genuine ambiguity, and still lands
  everything on a branch + PR for you to review. See [MCP server](#mcp-server)
  below for a full walkthrough.
- **"An entity page (a person, a project) has grown into an unreadable wall
  of history."** `wakil entities compile <slug>` re-synthesizes just the
  Compiled Truth section from the page's own Timeline, so the durable
  "current state" stays readable even as the evidence log underneath it
  grows indefinitely.

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

## Install a release

Tagged releases publish a wheel (`.whl`) and sdist (`.tar.gz`) as downloadable
assets on the [GitHub Releases](https://github.com/ebridges/wakil/releases)
page. The repo is currently private, so installing requires GitHub read
access (authenticated `git`/`gh`) — there's no public PyPI package yet.

```bash
# Option A — download the wheel asset from a release, then install it
uv tool install ./wakil-X.Y.Z-py3-none-any.whl
pip install ./wakil-X.Y.Z-py3-none-any.whl

# Option B — install straight from a tag, no manual download
uv tool install "git+https://github.com/ebridges/wakil@vX.Y.Z"
pip install "git+https://github.com/ebridges/wakil@vX.Y.Z"
```

## Command reference

Every command group in one place — jump to the section below for detail on
any of them. All commands accept the global `-w`/`--workspace` option; see
[Selecting a workspace](#selecting-a-workspace).

| Command | What it does |
| --- | --- |
| `wakil init <dir>` | Create a `.wakil/` workspace (config + SQLite db), index existing notes, register the workspace name. |
| `wakil status` | Notes indexed, git state, QMD availability. |
| `wakil index` | Re-index Markdown files after manual edits. |
| `wakil search <query>` | [QMD](#search) + SQLite FTS5 search over notes, memories, and sources. |
| `wakil query <question>` | [Grounded, cited answer](#query) from a model over search results + memory. |
| `wakil ingest transcript\|article\|text <path>` | [Step 1: capture](#ingest) a raw source. |
| `wakil enrich <source-id>` | [Step 2: analyze](#ingest) a capture and link it into the KB. |
| `wakil sources list\|show\|backfill-abstract` | Inspect captured sources; backfill title/abstract on old ones. |
| `wakil entities compile <slug>` | [Re-synthesize](#entities-compiled-pages) an entity page's Compiled Truth from its Timeline. |
| `wakil schema migrate\|validate\|list\|which` | [Entity frontmatter schema](#schema-tools) tools. |
| `wakil relationships <note-path>` | Walk the [note/memory relationship graph](#relationships) from an anchor note. |
| `wakil memory list\|show\|promote\|reject\|archive` | Review and manage the [memory lifecycle](#memory-lifecycle). |
| `wakil git summary\|history <path>` | [Git awareness](#git-native-changes): branch/PR state, file history. |
| `wakil skills list\|which\|validate` | Discover, inspect, and validate [skills](#skills). |
| `wakil qmd sync\|embed`, `wakil qmd collection add\|list\|remove` | Manage the QMD index/collections directly. |
| `wakil mcp serve` | Run wakil as an [MCP server](#mcp-server) for an MCP-speaking agent. |
| `wakil version` / `wakil --version` | Print the installed version. |

## Usage

```bash
# Initialize a knowledge-base workspace (creates .wakil/ with config + SQLite db,
# and registers the workspace name for -w lookups)
uv run wakil init ~/kb --name kb

# Show workspace status: notes indexed, git state, QMD availability
uv run wakil -w ~/kb status

# Re-index Markdown files after edits
uv run wakil -w kb index

# Search the knowledge base (QMD if installed, plus local FTS indexes)
uv run wakil -w kb search "insurance claims routing"

# Ask a grounded question (requires a model provider, see below)
uv run wakil -w kb query "How do my notes on FNOL relate to graph memory?"

# Step 1 — capture raw material (deterministic aside from one small model
# call that titles/abstracts it; requires a model provider, see below)
uv run wakil -w kb ingest transcript ./raw/meeting.txt \
    --context "Attendees: Jane Doe, Bob (Acme Corp). Weekly claims sync."
uv run wakil -w kb ingest article https://example.com/post
uv run wakil -w kb ingest text ./clipping.md

# Step 2 — analyze the captured source and link it into the KB
uv run wakil -w kb enrich 1

# Git-native ingest: commit on a wakil/ingest/* branch, optionally open a PR
uv run wakil -w kb ingest transcript ./raw/meeting.txt --branch
uv run wakil -w kb ingest article https://example.com/post --pr

# Git awareness
uv run wakil -w kb git summary
uv run wakil -w kb git history concepts/graph-memory.md

# Memory lifecycle
uv run wakil -w kb memory list --state candidate
uv run wakil -w kb memory show 3
uv run wakil -w kb memory promote 3 7
uv run wakil -w kb memory reject 4
uv run wakil -w kb memory archive 1
```

## Memory lifecycle

Ingests propose memories in `candidate` state; you review and decide:

```text
    working → candidate → durable
                      ↘ rejected
    durable → archived
```

`wakil memory list` (with `--state`/`--type` filters) reviews them, `promote`
moves them to durable, `reject` removes them from search, and `archive` keeps
them searchable but downranked. Invalid transitions are refused. Rather than
deleting, retrieval fades memories: durable ranks first, then candidate, then
working (fading further after 30 days), then archived — and, as a secondary
tiebreak *within* the same state, higher-confidence memories rank ahead of
lower-confidence ones. Memories used to answer a query get their
`last_seen_at` bumped for future ranking.

Memory `type` includes `fact | opinion | summary | relationship | question |
hypothesis | decision | theme | event` — `opinion` marks a subjective value
judgment or interpretation, as distinct from `fact`. Lifecycle `state`
transitions remain entirely manual (`promote`/`reject`/`archive`); there is
no automatic promotion or demotion based on type or confidence — a
low-confidence `opinion` still starts in `candidate` like everything else
and stays there until you act on it.

Memories also carry a `register` (`wakil memory list --register
formal|casual`) — a commitment axis orthogonal to `type`: `casual` marks a
low-commitment 1:1 "hot take" (a casual opinion and a casual fact are both
valid). `wakil query` excludes `casual` memories from the answer's grounding
context by default (`--include-casual` opts back in); `wakil search` is
unaffected — hot takes still surface there. See ADR 0014 for why the
underlying column/field is named `stance` rather than `register`.

## Selecting a workspace

Every command accepts a global `-w`/`--workspace` option (before the
subcommand) naming the workspace to operate on:

- **omitted** — use the current directory, searching upward for a `.wakil/`
  workspace (so any subdirectory of the knowledge base works);
- **a directory** — use that path (also searched upward);
- **a name** — look up a workspace registered by `wakil init` in
  `~/.config/wakil/workspaces.yaml`, so `wakil -w kb status` works from
  anywhere.

The `WAKIL_WORKSPACE` environment variable provides the same value as a
default, e.g. `export WAKIL_WORKSPACE=kb`.

## Skills

A skill is a directory containing a `SKILL.md` file (Claude-Skills-compatible
frontmatter: `name`, `skill_api`, optional `version`), plus any supporting
files it needs. wakil resolves a skill name by searching, in order,
`WAKIL_SKILL_PATH` (a `:`-separated list of extra roots), then
`<kb-root>/skills`, then `~/.config/wakil/skills`, then built-in skills — the
first directory found wins, and a broken override blocks fallback rather than
being silently skipped.

```bash
uv run wakil -w kb skills list                       # effective name + source
uv run wakil -w kb skills which meeting-synthesis -v  # selected path, roots, shadows
uv run wakil -w kb skills validate                    # check every root and skill
```

## Ingest

Ingest is two explicit steps.

**Step 1 — capture** (`wakil ingest transcript|text|article`) is deterministic
apart from one small model call that titles and abstracts the source (see
ADR 0010) — the raw file's path/filename is never model-derived. Text is
extracted (transcripts get light cleanup: bracketed and line-leading
timestamps removed, whitespace normalized — never model rewriting), deduped
by content hash, and written under `sources/` as a raw capture. `transcript`
also accepts a `.whisper` zip archive (Apple's diarized JSON export) in
addition to plain text/`.srt`. Transcript frontmatter is derived from the
`source` entity schema (`schema/entities/source.yaml`) — its base fields plus
its `transcript` origin sub-schema; known fields (title, abstract, meeting
date, create date, origin, url) are filled in, the rest are left as blank
placeholders. `--context`/`-C` accepts a few lines about the source
(attendees, company, purpose) and is stored on the source record for step 2.

If the computed destination path is already taken, capture refuses rather
than quietly writing `<name>-1.md` alongside it — pass `--overwrite` to
replace the existing file, or point at a different input. The warning appears
before the confirmation prompt, so `--yes` callers still see it, and an
overwriting run labels its preview `REPLACE` and its result line `replaced`
rather than presenting a destructive write as a new file.

`--overwrite` is for a file you put there yourself (a hand-cleaned transcript
sitting at its final path). If that path is already *another source's* raw
capture, capture refuses even with `--overwrite` and names the owning source:
writing there cannot move that source's pointer, so both would end up reading
the same text and `wakil enrich <old id>` would file its memories under the
wrong source. Rename the input so it lands on a different path, or move the
other source's file aside. The refusal is on the source record, not the file,
so it applies even when that source's file is no longer on disk.

Dates that appear in frontmatter and filenames (`created`, `retrieved`, a
recording's own date, the `YYYY-MM-DD-` filename prefix) use your machine's
local timezone, so an evening capture isn't stamped with tomorrow's date. Set
`timezone: <IANA name>` in `.wakil/config.yaml` to pin a specific zone —
useful when wakil runs on a host in a different zone than you. Database
timestamps remain UTC, as does the date in a `wakil/ingest/<date>-<slug>`
branch name.

If the input is a `.md` file that already carries its own YAML frontmatter, or
opens with an H1 title — a hand-cleaned transcript with a real title, date, and
tags — that file is treated as authored:

- its frontmatter wins over wakil's generated fields, except the ones wakil
  owns for a raw capture (`type`, `source_type`, `status`, and the `origin`/
  `url` provenance), and except any value the `source` schema rejects, which
  falls back to the generated one with a warning in the preview;
- no second frontmatter block is added, and the destination filename comes
  from the note's own title rather than the input's basename. A file that
  opens with its own H1 keeps that one and gets no second heading; a
  frontmatter-only file still gets wakil's generated `# <filename>` heading;
- an authored `meeting_date`/`date` beats the date wakil infers from the
  filename or the transcript's opening lines, which also changes the
  destination filename. `captured` is *not* read as a meeting date — it
  records when the file was captured;
- the timestamp-cleanup pass is skipped, so markers like `**[00:36]**` survive
  verbatim;
- if the file supplies both a title and an abstract, the capture-time model
  call is skipped entirely.

A heading partway down the file doesn't count — only a leading H1 — so an ASR
dump with section headings still gets the normal cleanup. Nor does a leading
`---` that isn't really a frontmatter fence: a block is only read as
frontmatter when it parses to a mapping whose keys all look like frontmatter
keys (lowercase, no spaces — your own vault's `attendees:`/`summary:` count
just as much as wakil's `title:`). A scratch note or a stretch of dialogue
between two `---` rules is kept as content instead of being parsed away, and
the preview says so — though a transcript still gets the normal cleanup pass.

When enrichment resolves an entity page that exists only on an earlier,
unmerged ingest branch — normal when you capture a cluster of related sources
before reviewing any PRs — it now exits non-zero naming that branch, instead
of reporting success with nothing written. The run is abandoned whole: no
memories are recorded and the source stays `raw`, so merging the branch and
re-running with `--force` completes the source rather than filing a second
copy of everything it extracted.

**Step 2 — enrichment** (`wakil enrich <source-id>`) is a fixed,
code-sequenced pipeline of two model calls, one preview, one confirm:

1. **Extraction** — judgment prose from `skills/<kind>/SKILL.md` (transcript,
   article, or text) produces the summary, key points, candidate memories
   (dated events carry their own `event_date`), relationships, and a proposed
   KB note. The capture-time context (or a fresh `--context`) plus the
   opening of the source drive a search for related entity notes and prior
   meetings, which the model links with [[wikilinks]]. Page shape and
   frontmatter come structurally from the entity-schema catalog; `RESOLVER.md`
   guidance, when present, is included so the note follows the KB's own
   subject-matter routing rules.
2. **Entity resolution** — always invoked, never optional: for each entity
   the source touched, the model decides create/update/skip against the
   existing notes and the shipped entity schemas. Notable new entities
   (action `create`) become stub pages with schema-valid frontmatter and a
   Compiled Truth / Timeline skeleton, routed into the type's canonical
   directory; the decisions are shown in the preview before anything is
   written.

Model output is validated against Pydantic contracts (the same JSON Schema
shown to the model), retried once on a validation failure, and fails visibly
— never silently coerced. Before apply, `validate_proposal()` checks every
proposed file's frontmatter against the entity schemas; a proposed `type:`
with no schema is a hard stop, not a best-guess write. Re-running requires
`--force`.

Both steps preview before writing (`--yes` skips the prompt) and accept
`--branch`/`--commit`/`--pr`; captures commit as `📥 wakil source:`, enrichment
as `🧠 wakil ingest:`. Proposed notes fall back to `drafts/` when routing is
unclear or the path collides; existing files are never overwritten. Extracted
memories are stored as `candidate` state for review with `wakil memory`.

`wakil sources list`/`show <id>` inspect captured sources (status, git
branch/PR landing state); `wakil sources backfill-abstract` retroactively
adds a title/abstract to sources captured before this feature existed —
metadata-only, it never re-runs enrichment.

## Git-native changes

When the knowledge base is a git repository, ingest can make its changes
reviewable git history:

- `--commit`/`-c` commits the ingested files on the current branch, staging
  only the files wakil wrote (your other uncommitted work is untouched);
- `--branch`/`-b` first checks the working tree is clean, then creates a
  `wakil/ingest/<date>-<slug>` branch and commits there;
- `--pr` additionally pushes the branch and opens a pull request via the
  GitHub CLI (`gh`), when it is installed and an `origin` remote exists.

Commits follow the wakil conventions (`🧠 wakil ingest: add ...`) and every
commit is recorded in the workspace database (`git_changes`). `wakil git
summary` shows the current branch, pending changes, recent commits, and
wakil-created branches; `wakil git history <path>` shows one file's history.

Only one wakil process at a time can write to a given checkout. Every command
that writes files or commits — `ingest`, `enrich`, `schema migrate`,
`entities compile`, `sources backfill-abstract` — takes an advisory lock (under `.wakil/locks/`) around the
whole write → commit → return sequence, so two sessions can't interleave their
checkouts and clobber each other's uncommitted work. Read-only commands
(`search`, `query`, `status`, `sources list`) don't contend. A second process
fails immediately with the holder's pid rather than waiting; set
`WAKIL_GIT_LOCK_TIMEOUT=<seconds>` to make it wait instead. Separate
`git worktree` checkouts of the same repository lock independently, so they
still run in parallel. If a lock seems stuck, check for a leftover
`wakil mcp serve` — the lock is released automatically when its holder exits,
including on a crash, so there is never a stale lock file to clean up.

Every commit is verified to be landing on the branch wakil resolved for that
source: if something moved the working tree in between (a concurrent wakil
process, or your own `git switch`), the command refuses to commit and says so,
rather than committing wherever HEAD happens to be. Afterwards the working
tree is left on the repository's default branch.

If your repository signs commits (SSH signing via a hardware key or 1Password),
`git commit` blocks on an interactive approval prompt. wakil allows 10 minutes
for that step; set `WAKIL_GIT_COMMIT_TIMEOUT` (seconds) to change it.

Any commit failure leaves you on the ingest branch with the work staged, not
just a timeout — a rejecting `pre-commit` hook or a signing error does the same,
so that you can fix the cause and commit in place rather than reconstructing
the message from another branch. A killed commit gets the extra cleanup below.

If the commit does time out, wakil stays on the ingest branch with your changes
still staged, clears the `.git/index.lock` the killed process leaves behind
(without which every later git command in the repo fails), and saves the commit
message it was going to use. The error prints the exact command to finish by
hand — run it from that branch, without switching away first:

```bash
git -C <workspace> commit -F <workspace>/.git/COMMIT_EDITMSG -- <the files it listed>
```

The printed command includes the file list; keep it, so the commit stays
scoped to wakil's own files rather than everything you have staged.

The push and pull request don't happen in that case, so `wakil git summary`
won't show the change until either you push the branch and open the PR
yourself, or you re-run `wakil enrich <id>` once the commit is finished — the
source already remembers this branch, so the next run resumes onto it and
lands the PR from there.

## Entities: compiled pages

An entity page's `## Compiled Truth` section is meant to hold the current,
synthesized state; its `## Timeline / Log` is an append-only evidence trail
that only ever grows. Over enough `enrich` passes, Compiled Truth itself can
grow unwieldy. `wakil entities compile <slug>` re-synthesizes just the
Compiled Truth section from the page's own Timeline (never from external
sources), previews the rewrite, and — if it's still over the target size —
offers an interactive menu to apply as-is, hand-edit, force a full
resynthesis, or cancel. `--commit` records the rewrite as a
`♻️ wakil chore:` commit. See ADR 0017 for the full design.

## Schema tools

Entity frontmatter shape lives in `schema/entities/*.yaml` (13 built-in
types), resolved with the same override precedence as skills (kb-local, then
user config, then built-in). `wakil schema list` shows effective types and
their source; `wakil schema which <type>` shows which root wins for one
type; `wakil schema validate <paths>` checks arbitrary files' frontmatter
against the schemas — the same check `wakil enrich` runs before writing,
useful against files a skill wrote by hand outside the enrichment pipeline.
`wakil schema migrate` applies cheap, mechanical fixes (field renames,
exact-duplicate drops, type normalization) across existing notes, behind
`--dry-run`/`--yes`/`--commit`.

### Page shapes: how a note's body is structured

Frontmatter fields are only half of an entity schema — each entity type also
declares a `page_shape` naming which body template `wakil enrich` must
follow when it writes or updates that type's note. There are two shapes,
each a template file under
[`src/wakil/schema/templates/`](src/wakil/schema/templates/):

- [`compiled-truth-timeline.md`](src/wakil/schema/templates/compiled-truth-timeline.md)
  — for an accumulating subject touched by multiple sources over time (a
  `person`, `company`, `project`, `concept`, `index`, `journal`,
  `assessment`). A `## Compiled Truth` section is *re-synthesized* on every
  update to cover everything known so far — never replaced with just the
  newest source's content — while `## Timeline / Log` is *append-only*:
  new dated entries are added, existing ones are never deleted, rewritten,
  or reordered. `wakil entities compile` (see
  [above](#entities-compiled-pages)) only ever touches this shape's
  Compiled Truth section.
- [`single-occurrence.md`](src/wakil/schema/templates/single-occurrence.md)
  — for a note describing one dated event or standalone artifact, not an
  accumulating subject (a `meeting`, `reflection`, `idea`, `organization`,
  `meta`, `source`). There's no Timeline here — a running log of a single
  occurrence would just be the occurrence restated — so the template is a
  flat `Summary`/`Key Decisions`/`Action Items`/`Discussion Notes`/`Open
  Questions` skeleton, with guidance on which sections are optional per type.

Which built-in type uses which shape (`wakil schema list` shows this for
your workspace's actual resolved set, including any kb-local overrides):

| Shape | Types |
| --- | --- |
| `compiled-truth-timeline` | `person`, `company`, `project`, `concept`, `index`, `journal`, `assessment` |
| `single-occurrence` | `meeting`, `reflection`, `idea`, `organization`, `meta`, `source` |

Templates resolve with the same kb-local → user-config → built-in override
precedence as skills and entity schemas (`resolve_page_shape_template` in
`schema/loader.py`) — a workspace can supply its own
`schema/templates/<shape>.md` to change the narrative structure a shape
enforces, without touching wakil's own code. The resolved template body is
injected directly into `wakil enrich`'s model prompt, so this isn't just
documentation — it's the actual instruction the model follows for body
structure, the same way frontmatter fields are the instruction for metadata
shape.

## Search

`wakil search` combines two engines: [QMD](https://github.com/tobi/qmd) over
the Markdown knowledge base when the `qmd` binary is installed (`--mode
search|vsearch|query` selects BM25, vector, or hybrid), and SQLite FTS5
indexes over note metadata, memories, and sources. QMD results take
precedence; FTS fills in workspace records QMD doesn't cover. `wakil qmd
sync`/`embed` and `wakil qmd collection add|list|remove` manage the
underlying QMD index directly, when you need more control than `wakil init`'s
automatic setup gives you.

## Query

`wakil query` retrieves matching notes, memories, and sources, sends them to a
model as numbered context blocks, and prints a cited answer. Answers are
grounded: if the knowledge base doesn't support an answer, wakil says so.
Each query is recorded in the workspace database (`query_runs`).
Casual-register memories are excluded from grounding by default; pass
`--include-casual` to let a hot take answer a question anyway.

Configure a provider via environment variables (see `.env.example`):

- `ANTHROPIC_API_KEY` — uses Anthropic (default model `claude-opus-4-8`)
- `OPENAI_API_KEY` + `WAKIL_MODEL` (+ optional `WAKIL_OPENAI_BASE_URL`) — any
  OpenAI-compatible endpoint, including local model servers
- `WAKIL_MODEL` / `WAKIL_PROVIDER` — override the model or force a provider

`wakil init` indexes every Markdown file in the workspace (title, frontmatter,
content hash), detects whether the directory is a git repository, checks for
QMD on the PATH, and records high-priority context files (`README.md`,
`AGENTS.md`, `RESOLVER.md`) when present.

### Relationships

`wakil relationships <note-path>` walks the same note/memory relationship
graph query grounding draws from — a SQLite recursive CTE over `mentions`
(automatic, from `[[wikilinks]]` found at index time) and model-proposed
relationship edges. `--direction out|in|both`, `--predicate`, and `--depth`
narrow the walk from a single anchor note.

## MCP server

`wakil mcp serve` runs wakil as an [MCP](https://modelcontextprotocol.io)
server over stdio, bound to one workspace for the life of the process (same
`-w/--workspace` resolution as every other command). It exposes read tools
(`status`, `search`, `query`, `memory_list`/`show`, `relationships`,
`sources_list`/`show`, `git_summary`/`history`, `skills_list`) plus two
prepare/apply tool pairs for writes — `ingest_prepare`/`ingest_apply` and
`enrich_prepare`/`enrich_apply` — mirroring the CLI's own preview-then-
confirm flow: `*_prepare` returns a preview and nothing is written until a
separate `*_apply` call. See ADR 0018 for the full tool list and design.

Point an MCP client's config at it, e.g. for Claude Desktop
(`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "wakil": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/wakil", "wakil", "-w", "kb", "mcp", "serve"]
    }
  }
}
```

### Using the mcp-coordinator skill for fast capture

For fast, low-friction capture (useful when you're moving between meetings
and don't want to review every field), have the connected agent follow
`skills/mcp-coordinator/SKILL.md` — also served live by the running server
as the `wakil://skill/mcp-coordinator` MCP resource, so **no manual
install is needed**: any MCP client that can read resources from a connected
server can load the skill directly from `wakil mcp serve` itself.

In practice, once `wakil` is configured as an MCP server, you just tell your
agent what you want in plain language, e.g.:

> Follow the wakil mcp-coordinator skill and capture this transcript, then
> enrich it: `~/Downloads/2026-07-30-claims-sync.txt`. Attendees: Jane Doe,
> Bob (Acme Corp).

The agent reads the coordinator skill, then chains `ingest_prepare` straight
into `ingest_apply`, and `enrich_prepare` straight into `enrich_apply`, for
the routine case — it only stops to ask you something when it hits genuine
ambiguity (a plausible entity-duplicate, a low-confidence/peripheral flag, a
validation issue). Either way, everything still lands on a branch and a pull
request, which remains the review checkpoint whether a human or an agent
drove the capture. See ADR 0019 for why that's an acceptable substitute for
a pre-write pause, not a bypass of one.

## Development

```bash
uv run pytest             # tests
uv run ruff check         # lint
uv run ty check           # type-check (uvx ty check if ty isn't installed locally)
uv run pytest -m eval     # live-model skill evals (needs ANTHROPIC_API_KEY or another configured provider; skipped if none is set)
```

Design decisions are recorded as ADRs under `docs/adr/` (with `docs/adr/0000-template.md`
establishing how they're structured); recurring dev
patterns and known gotchas live in `docs/DEVELOPMENT.md` and
`docs/TROUBLESHOOTING.md` — see `CLAUDE.md` for when/how those get updated.
Releases are cut via the `Release` GitHub Actions workflow
(`workflow_dispatch`, choose a `patch`/`minor`/`major` bump); see
`CHANGELOG.md` for release history.

Every non-draft PR also gets an automated review comment from the
`pr-reviewer` subagent (`.github/workflows/pr-review.yml`), applying the
same staff-level, ADR-aware review it runs on request — see
`.claude/agents/pr-reviewer.md` for what it checks.
