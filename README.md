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

# Ingest raw material into the knowledge base
uv run wakil -w kb ingest transcript ./raw/meeting.txt
uv run wakil -w kb ingest article https://example.com/post
uv run wakil -w kb ingest text ./clipping.md

# Git-native ingest: commit on a wakil/ingest/* branch, optionally open a PR
uv run wakil -w kb ingest transcript ./raw/meeting.txt --branch
uv run wakil -w kb ingest article https://example.com/post --pr

# Git awareness
uv run wakil -w kb git summary
uv run wakil -w kb git history concepts/graph-memory.md
```

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

## Ingest

`wakil ingest` turns raw material into knowledge-base records. It extracts
text (transcripts, `.srt` subtitles, plain text, or web articles via
readability extraction), dedupes by content hash, finds related existing
notes, and — when a model provider is configured — produces a summary, key
points, candidate memories, candidate relationships, and an optional proposed
Markdown note linked with wikilinks.

Nothing is written until you confirm the preview (`--yes` skips the prompt).
The raw capture lands under `sources/` with frontmatter; proposed notes go to
the model-suggested path, falling back to `drafts/` when routing is unclear or
the path collides. Existing files are never overwritten, and all writes are
plain files you can review with `git diff`/`git status`. Extracted memories
are stored as `candidate` state in SQLite for later review and promotion.
Without a provider, ingest still stores the source and raw capture.

## Git-native changes

When the knowledge base is a git repository, ingest can make its changes
reviewable git history:

- `--commit`/`-c` commits the ingested files on the current branch, staging
  only the files wakil wrote (your other uncommitted work is untouched);
- `--branch`/`-b` first checks the working tree is clean, then creates a
  `wakil/ingest/<date>-<slug>` branch and commits there;
- `--pr` additionally pushes the branch and opens a pull request via the
  GitHub CLI (`gh`), when it is installed and an `origin` remote exists.

Commits follow the wakil conventions (`wakil ingest: add ...`) and every
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

Configure a provider via environment variables (see `.env.example`):

- `ANTHROPIC_API_KEY` — uses Anthropic (default model `claude-opus-4-8`)
- `OPENAI_API_KEY` + `WAKIL_MODEL` (+ optional `WAKIL_OPENAI_BASE_URL`) — any
  OpenAI-compatible endpoint, including local model servers
- `WAKIL_MODEL` / `WAKIL_PROVIDER` — override the model or force a provider

`wakil init` indexes every Markdown file in the workspace (title, frontmatter,
content hash), detects whether the directory is a git repository, checks for
QMD on the PATH, and records high-priority context files (`README.md`,
`AGENTS.md`, `SCHEMA.md`, `RESOLVER.md`) when present.

## Development

```bash
uv run pytest       # tests
uv run ruff check   # lint
```
