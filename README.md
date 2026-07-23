# Wakil

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
operational index; git provides history and review. See `PROMPT.md` for the
full plan and `TODO.md` for what's next.

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

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

    working → candidate → durable
                      ↘ rejected
    durable → archived

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
by content hash, and written under `sources/` as a raw capture. Transcript
frontmatter is derived from the `source` entity schema
(`schema/entities/source.yaml`) — its base fields plus its `transcript`
origin sub-schema; known fields (title, abstract, meeting date, create date,
origin, url) are filled in, the rest are left as blank placeholders.
`--context`/`-C` accepts a few lines about the source (attendees, company,
purpose) and is stored on the source record for step 2.

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

`wakil sources backfill-abstract` retroactively adds a title/abstract to
sources captured before this feature existed — metadata-only, it never
re-runs enrichment.

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

## Search

`wakil search` combines two engines: [QMD](https://github.com/tobi/qmd) over
the Markdown knowledge base when the `qmd` binary is installed (`--mode
search|vsearch|query` selects BM25, vector, or hybrid), and SQLite FTS5
indexes over note metadata, memories, and sources. QMD results take
precedence; FTS fills in workspace records QMD doesn't cover.

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

## Development

```bash
uv run pytest             # tests
uv run ruff check         # lint
uv run pytest -m eval     # live-model skill evals (needs ANTHROPIC_API_KEY or another configured provider; skipped if none is set)
```
