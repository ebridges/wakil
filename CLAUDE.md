## Project

This repository contains `wakil`, a local-first Python CLI agent for working with a personal Markdown knowledge base.

`wakil` is designed for one user working locally on knowledge work, not coding automation. The target knowledge base is GBrain / Obsidian style: Markdown files, links, concepts, notes, meeting transcripts, web clippings, source material, journal entries, and synthesis documents.

The goal is to help the user ingest, search, connect, revise, and reason over a Markdown knowledge base.

The project thesis is:

> Build a local, git-native knowledge-work agent that helps a single user discover useful connections, maintain a Markdown knowledge base, and turn raw inputs into durable, searchable memory.

The engineering principle is:

> Keep the implementation simple unless added complexity has a clear and self-evident impact on the target use case.

Do not over-engineer this project.

---

## Python Stack

Use modern Python and boring local infrastructure.

```text id="olf5zd"
Python 3.12+
uv
Pydantic v2
Typer
Rich / Textual-friendly output patterns
SQLite
SQLAlchemy
Alembic
httpx
BeautifulSoup / readability-lxml for article extraction
python-frontmatter
markdown-it-py
PyYAML
GitPython or subprocess git wrapper
pytest
ruff
ty
```

Model access should be provider-abstracted but minimal:

```text id="p1muyf"
OpenAI-compatible endpoint
Anthropic
Gemini or other provider later only if needed
local model endpoint later if useful
```

Search:

```text id="lch4np"
QMD as first-class search
SQLite FTS5 for internal records
optional embeddings later
optional Neo4J deep dive later
```


---

## Prime Directive

Build the smallest useful version of `wakil` that advances the local knowledge-work use case.

Before adding abstraction, ask:

```
Does this clearly improve local Markdown knowledge work for one user?
```

If the answer is not obviously yes, do not add it.

Prefer simple, explicit, boring Python code over clever frameworks or speculative architecture.

---

## Design Biases

Prefer:

```
simple local workflows
Markdown as source of truth
SQLite as operational store
QMD as first-class search
git-native changes
clear memory lifecycle
human review
rich CLI output
grounded citations
explicit commands
small composable services
```

Avoid:

```
remote runtimes
complex permissions
large agent frameworks
hidden background behavior
automatic rewriting without review
multi-agent orchestration
premature graph databases
opaque memory systems
overly abstract plugin architectures
hosted product assumptions
```

---

## Working Agreement for Agents

When contributing to this repo:

1. Keep changes small.
2. Do not add speculative abstractions.
3. Preserve the local-first CLI focus.
4. Prefer Markdown, SQLite, QMD, and Git over new infrastructure.
5. Avoid building for multi-user or hosted deployment yet.
6. Keep model behavior behind deterministic application controls.
7. Add tests for meaningful behavior.
8. Update docs when behavior changes.
9. Respect the distinction between raw sources, durable notes, and memory.
10. Treat sensitive content carefully.
11. Show reviewable diffs for knowledge-base modifications.
12. Do not silently rewrite user knowledge.

The project should feel like a careful local knowledge assistant, not a sprawling agent platform.
