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
# Initialize a knowledge-base workspace (creates .wakil/ with config + SQLite db)
uv run wakil init ~/kb

# Show workspace status: notes indexed, git state, QMD availability
uv run wakil status --path ~/kb

# Re-index Markdown files after edits
uv run wakil index --path ~/kb
```

`wakil init` indexes every Markdown file in the workspace (title, frontmatter,
content hash), detects whether the directory is a git repository, checks for
QMD on the PATH, and records high-priority context files (`README.md`,
`AGENTS.md`, `SCHEMA.md`, `RESOLVER.md`) when present.

## Development

```bash
uv run pytest       # tests
uv run ruff check   # lint
```
