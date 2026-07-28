# wakil: Local Knowledge-Work Agent Plan

## Purpose

`wakil` is a local-first AI agent for working with a personal Markdown knowledge base.

The goal is not to build a broad agent platform, a coding assistant, or a complex multi-runtime system. The goal is to build a simple, pragmatic, powerful local assistant that helps a single user ingest, search, connect, revise, and reason over a Markdown knowledge base.

The target knowledge base is GBrain / Obsidian style: Markdown files, links, concepts, notes, project material, meeting transcripts, web clippings, and other personal knowledge artifacts.

The guiding product thesis is:

> Build a local, git-native knowledge-work agent that helps a single user discover useful connections, maintain a Markdown knowledge base, and turn raw inputs into durable, searchable memory.

The guiding engineering principle is:

> Keep the implementation simple unless added complexity has a clear and self-evident impact on the target use case.

Do not over-engineer this project.

Any additional abstraction, service, datastore, runtime, interface, or agent behavior must directly support the local knowledge-work use case.

---

## Name

The project is named:

```text id="rgwvfs"
wakil
```

`wakil` should be treated as a local assistant, representative, or advocate for the user’s knowledge base.

---

## Target Use Case

The target user is a single person working locally with a personal Markdown knowledge base.

The core use case is not coding.

The core use case is knowledge work:

1. Ingest meeting transcripts.
2. Ingest meeting recordings after transcription.
3. Ingest Twitter/X links.
4. Ingest web articles.
5. Ingest pasted notes, outlines, and rough ideas.
6. Search existing Markdown knowledge.
7. Discover relationships between notes, people, topics, decisions, and ideas.
8. Generate useful summaries, briefs, maps, and follow-up notes.
9. Propose edits to the Markdown knowledge base.
10. Use git history to understand how the knowledge base has evolved.
11. Use pull requests or branches for larger ingests and modifications.
12. Run deeper reflection or “dream” passes that create unexpected but useful connections.
13. Use preexisting Claude or Hermes skills.

The platform should be built for one local user first, while keeping the data model ready for multiple users later.

That single user is not always sitting still with time to review every
step. A recurring version of this user is a busy operator — someone moving
between meetings across a high-meeting-volume day — who needs the
*interruption cost* of getting a transcript or a note into the knowledge
base to stay low, even though the underlying mechanism (prepare a change,
review it, land it on a branch/PR) doesn't change. `wakil mcp serve`
(docs/adr/0018) plus the `mcp-coordinator` skill (docs/adr/0019) exist for
this version of the user: the same prepare/apply checkpoints as the CLI,
chained quickly for routine cases and paused only for genuine ambiguity.

---

## Explicit Non-Goals

Do not build these in the initial version:

```text id="2qv19p"
coding agent workflows
remote Docker execution
multi-user hosted web app
Slack gateway
email gateway
browser automation framework
multi-agent swarms
desktop app
mobile app
complex distributed job system
fine-tuning
marketplace
large plugin architecture
enterprise permissions
cloud-first architecture
```

The first version should be a rich local CLI for a single user working with a Markdown knowledge base.

---

## Product Shape

`wakil` should feel like a thoughtful command-line companion for a personal knowledge base.

The CLI should be the primary interface.

It should be rich and pleasant to use:

* clear commands;
* helpful previews;
* colorized diffs;
* progress indicators;
* structured tables;
* readable Markdown output;
* interactive confirmations;
* branch / PR guidance;
* summaries that are easy to paste back into notes;
* citations to source files, sections, commits, or ingested artifacts.

The first version should not require a server, browser UI, or remote runtime.

However, the design should not prevent additional interfaces later. The
first such interface is `wakil mcp serve` (docs/adr/0018) — an MCP server,
still local, still bound to one workspace, that lets an MCP-speaking agent
call the same prepare/apply operations the CLI exposes. "Human review"
means either a pre-write preview (direct CLI/tool use) or the resulting
pull request (an agent following the `mcp-coordinator` skill,
docs/adr/0019) — both are the same underlying checkpoint at a different
tempo, not two different standards.

---

## Core Concepts

### Knowledge Base

The knowledge base is a local Markdown directory.

It may follow GBrain, Obsidian, or similar conventions.

A knowledge base contains:

```text id="gw0j64"
Markdown notes
internal links
tags
frontmatter
attachments
transcripts
ingested articles
source metadata
concept notes
project notes
people notes
meeting notes
daily notes
decision records
```

`wakil` should treat the Markdown knowledge base as the source of truth.

SQLite is an index, cache, memory store, and operational database. It should not replace the Markdown files.

### Workspace

A workspace is a local knowledge-base checkout.

A workspace contains:

```text id="2rc70v"
Workspace
  id
  name
  root_path
  git_remote
  qmd_config
  memory_database
  ingest_directory
  generated_directory
  conventions
```

The workspace boundary matters. `wakil` should not casually operate outside the configured knowledge base directory.

### Source

A source is raw material brought into the knowledge base.

Examples:

```text id="nhhyb0"
meeting transcript
meeting recording
Twitter/X link
web article
PDF
pasted note
audio transcript
email export
GitHub issue
existing Markdown file
```

Each source should have metadata:

```text id="ityl60"
source type
origin URL or file path
retrieved_at
author if known
published_at if known
title
content hash
ingest status
related notes
```

### Note

A note is a Markdown file in the knowledge base.

Notes are durable user-facing knowledge artifacts.

`wakil` may create or edit notes, but changes should be git-visible and reviewable.

### Memory

Memory should be uniform and easy to reason about.

A memory is a structured claim, observation, relationship, summary, or working context derived from sources and notes.

All memory should share one core model, even if different memory records have different lifecycle states.

A memory record may represent:

```text id="k5j1ed"
a fact
a summary
a relationship
a question
a hypothesis
a user preference
a project convention
a note-level embedding target
a source-level summary
a recurring theme
a decision
an unresolved thread
```

### Skill

A skill is a reusable Markdown procedure that teaches `wakil` how to perform a recurring knowledge-work task.

Examples:

```text id="3fnnwq"
ingest a meeting transcript
summarize an article
extract people and organizations
create an evergreen concept note
find connections between two topics
prepare a weekly synthesis
run a dream pass
```

Skills should remain simple Markdown files, and use the same structure, conventions and format as Claude skills so that existing skills can be "dropped in" and used.

---

## Anti-Overengineering Rule

Every added component must pass this test:

```text id="5q4s95"
Does this clearly improve local Markdown knowledge work for one user?
```

If the answer is not obviously yes, do not build it yet.

Examples:

| Idea                   |    Build Now? | Reason                                            |
| ---------------------- | ------------: | ------------------------------------------------- |
| Rich CLI               |           Yes | Primary product surface                           |
| SQLite                 |           Yes | Simple local persistence and indexing             |
| QMD integration        |           Yes | First-class search mechanism                      |
| Git integration        |           Yes | Knowledge base is file-native and history matters |
| Docker runtime         |            No | Not needed for local knowledge work               |
| Hosted web app         |            No | Not needed for first version                      |
| Multi-user auth        |            No | Data model only should be ready                   |
| Neo4J                  |   Maybe later | Useful only if graph exploration proves valuable  |
| Background daemon      | Not initially | Use explicit CLI commands first                   |
| Multi-agent delegation |            No | Adds complexity without immediate need            |


Maintain a `TODO.md` with items to work on in the future.

---

## Architecture

The initial architecture should be deliberately simple.

```text id="nca2l1"
CLI
  commands, prompts, rich output

Application Services
  ingest
  search
  query
  memory
  dream
  notes
  git
  qmd

Local Storage
  SQLite
  Markdown files
  Git repository
  QMD index/config

Model Layer
  provider abstraction
  prompt builders
  structured outputs

Integrations
  QMD
  Git
  GitHub CLI
  web article fetcher
  transcript/audio tooling
```

There should be no runtime abstraction beyond what is necessary to keep local execution clean.

Do not build Docker, remote execution, or hosted services in the first version.

---

## Suggested Python Stack

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

## Proposed Repository Structure

```text id="9rx558"
wakil/
  pyproject.toml
  README.md
  .env.example

  src/
    wakil/
      __init__.py

      cli/
        __init__.py
        main.py
        commands/
          init.py
          ingest.py
          query.py
          search.py
          dream.py
          memory.py
          notes.py
          git.py
          status.py

      app/
        __init__.py
        ingest_service.py
        query_service.py
        search_service.py
        memory_service.py
        dream_service.py
        note_service.py
        git_service.py
        qmd_service.py

      models/
        __init__.py
        workspace.py
        source.py
        note.py
        memory.py
        relationship.py
        ingest.py
        query.py

      llm/
        __init__.py
        client.py
        openai_compatible.py
        prompts.py
        structured_outputs.py

      storage/
        __init__.py
        database.py
        schema.py
        repositories.py

      knowledge/
        __init__.py
        markdown.py
        frontmatter.py
        links.py
        tags.py
        chunks.py
        citations.py

      integrations/
        __init__.py
        qmd.py
        git.py
        github.py
        web.py
        audio.py

      skills/
        __init__.py
        loader.py
        registry.py

      config/
        __init__.py
        settings.py
        workspace_config.py

      ui/
        __init__.py
        console.py
        tables.py
        diffs.py
        prompts.py

  skills/
    ingest_transcript.md
    ingest_article.md
    synthesize_topic.md
    dream.md

  docs/
    architecture.md
    memory.md
    git-native-workflow.md
    qmd-integration.md
    dream-function.md

  tests/
    unit/
    integration/
    fixtures/
```

Keep this structure flexible. Do not create empty modules prematurely unless they clarify the project layout.

---

## SQLite Data Model

The data model should be ready for multiple users later, but the application should run as a single-user local tool now.

The minimum useful entities:

```text id="609czx"
User
Workspace
Source
Note
Memory
Relationship
IngestRun
QueryRun
GitChange
Skill
```

### User

Single-user mode can create a default user automatically.

```text id="3qk052"
id
display_name
created_at
```

### Workspace

```text id="ycsxcd"
id
user_id
name
root_path
git_remote
qmd_enabled
created_at
updated_at
```

### Source

A raw ingested item.

```text id="0ljdbu"
id
workspace_id
source_type
title
origin
author
published_at
retrieved_at
content_hash
raw_text_path
status
metadata_json
created_at
updated_at
```

### Note

An indexed Markdown file.

```text id="qy1i7k"
id
workspace_id
path
title
frontmatter_json
content_hash
last_indexed_at
created_at
updated_at
```

### Memory

A uniform memory record.

```text id="im5jio"
id
workspace_id
user_id
memory_type
content
summary
source_id
note_id
confidence
state
importance
freshness
last_seen_at
created_at
updated_at
metadata_json
```

Recommended memory states:

```text id="jiay8i"
working
candidate
durable
archived
rejected
```

Recommended memory types:

```text id="25x8ff"
fact
summary
relationship
question
hypothesis
decision
theme
preference
procedure
```

This gives a uniform memory model while allowing different lifecycles.

### Relationship

Relationships should be simple at first.

```text id="j9huu7"
id
workspace_id
subject_memory_id
predicate
object_memory_id
source_id
note_id
confidence
created_at
metadata_json
```

Example predicates:

```text id="jccxpc"
supports
contradicts
elaborates
mentions
caused_by
related_to
similar_to
depends_on
decided_by
raises_question
```

Do not build a complex ontology early.

### IngestRun

```text id="bjui7j"
id
workspace_id
source_id
status
started_at
completed_at
created_branch
created_commit
created_pr_url
summary
error
metadata_json
```

### QueryRun

```text id="gr1r5d"
id
workspace_id
query
status
started_at
completed_at
sources_used_json
notes_used_json
memories_used_json
answer
metadata_json
```

### GitChange

```text id="nul3k3"
id
workspace_id
operation
branch_name
commit_sha
pr_url
summary
created_at
metadata_json
```

---

## Memory Model

Memory is central to `wakil`.

The model should be uniform, but memory should have lifecycle behavior.

### Memory Lifecycle

Use a simple lifecycle:

```text id="fqhcpc"
working → candidate → durable
                  ↘ rejected
durable → archived
```

Definitions:

| State       | Meaning                                                    |
| ----------- | ---------------------------------------------------------- |
| `working`   | Temporary context from a session, query, or ingest         |
| `candidate` | Potentially useful memory proposed by the system           |
| `durable`   | Approved or promoted memory that should affect future work |
| `rejected`  | Memory proposal that should not be used                    |
| `archived`  | Old memory retained for history but downranked             |

This allows memory to “fade” without inventing too much machinery.

### Fading

Rather than delete memory, `wakil` should reduce the retrieval priority of memory that is old, weak, unreferenced, or never promoted.

A simple ranking formula can combine:

```text id="ptz8zd"
state
importance
freshness
confidence
recency
number of references
whether linked to a durable note
whether confirmed by user action
```

Working memories should naturally fade unless they are promoted.

Candidate memories should remain visible for review but not dominate search.

Durable memories should be favored.

Archived memories should be searchable but not prominent.

Memories should be vector indexed for similarity search, and their applicability weighted by their relevance, recency, confidence, and number of references.

### Promotion

Memory can migrate to long-term durable memory through:

```text id="1uystx"
explicit user approval
being incorporated into a Markdown note
being referenced repeatedly
being produced by a trusted skill
being confirmed during a dream pass
```

Do not make this too magical at first.

In the initial version, explicit promotion is enough.

---

## Dream Function

A “dream” function is a deeper synthesis pass inspired by GBrain.

The purpose of dreaming is not to hallucinate. The purpose is to search the knowledge base in a less literal way and propose useful connections, questions, and synthesis notes.

A dream pass may:

```text id="uzlsog"
review recent working and candidate memories
search QMD for related notes
look for recurring themes
find weak or surprising relationships
cluster related ideas
identify unresolved questions
propose new concept notes
propose links between existing notes
suggest memories to promote or archive
produce a human-readable dream report
```

The dream output should be reviewable.

A dream should not automatically rewrite the knowledge base unless explicitly approved.

Initial dream command:

```bash id="jwpq32"
wakil dream
```

Useful variants:

```bash id="08y0u2"
wakil dream --recent
wakil dream --topic "insurance claims automation"
wakil dream --since "2 weeks ago"
wakil dream --source transcript.md
wakil dream --write-report
wakil dream --propose-links
```

Dream should produce:

```text id="qtqtgj"
summary
interesting connections
possible contradictions
open questions
suggested durable memories
suggested note links
suggested notes to create
source citations
```

Dream is a good place to explore more sophisticated memory behavior later.

---

## Search and Relationship Discovery

Search and discovery are core.

`wakil` should use QMD as a first-class search mechanism.

### Search Sources

Use multiple simple search paths:

```text id="pmtlaf"
QMD search over Markdown knowledge base
SQLite FTS5 over sources, memories, query runs, and indexed note metadata
git history search
optional relationship table traversal
```

Initial search should be hybrid but pragmatic.

Do not build an elaborate RAG framework.

### QMD Integration

QMD should be treated as the primary knowledge-base search engine.

`wakil` should be able to:

```text id="p6y7e8"
detect QMD config
run QMD queries
map QMD results back to note paths
show QMD results in CLI output
use QMD results as context for LLM queries
combine QMD results with SQLite memory records
```

The QMD integration should be encapsulated so that the rest of the app does not depend on shell command details.

### Relationship Discovery

Relationship discovery should begin simply.

For an ingest or query, `wakil` can extract candidate relationships like:

```text id="xl0cne"
Source A mentions Concept B
Note A relates to Note B
Memory A supports Memory B
Memory A raises Question B
Topic A appears in the same context as Topic B
```

Relationships should be treated as candidate signals unless promoted or repeatedly supported.

Do not require Neo4J initially.

---

## Optional Neo4J Deep Dive

Neo4J may become valuable if relationship exploration becomes central.

Potential benefits:

```text id="4ka4h4"
visual graph exploration
multi-hop relationship queries
concept clustering
people / project / topic networks
stronger dream passes
relationship-heavy browsing
```

Reasons not to add it initially:

```text id="f9qmar"
adds operational complexity
requires graph modeling decisions too early
duplicates what SQLite can handle at small scale
may distract from the Markdown-first workflow
```

Recommended approach:

1. Start with SQLite relationship tables.
2. Build useful relationship extraction and review.
3. Evaluate whether SQLite becomes limiting.
4. Add optional Neo4J export or sync only if graph workflows prove valuable.

Initial stance:

```text id="h7zmyh"
SQLite first. Neo4J later as an optional enhancement, not a core dependency.
```

---

## Git-Native Workflow

`wakil` should be git-native.

The Markdown knowledge base should be treated like a real repository.

### Git Awareness

`wakil` should understand:

```text id="kf911q"
current branch
dirty working tree
recent commits
file history
commit authorship
changed notes
renamed notes
deleted notes
remote status
open pull requests if GitHub is configured
```

### Ingest Branches

For meaningful ingests, `wakil` should create a branch.

Example:

```bash id="5g4n6f"
wakil ingest transcript ./raw/meeting.txt --branch
```

Possible branch name:

```text id="p1oypq"
wakil/ingest/2026-07-09-meeting-summary
```

### Commit Conventions

Use simple commit conventions specific to knowledge work.

Examples:

```text id="agv0s9"
wakil ingest: add transcript summary for product strategy meeting
wakil note: update claims automation concept map
wakil link: connect FNOL notes to agent-routing notes
wakil memory: promote durable claims-processing insight
wakil dream: add synthesis report for insurance automation
wakil source: archive raw article on AI agents
```

Suggested commit prefixes:

```text id="o9jaja"
wakil ingest:
wakil note:
wakil link:
wakil memory:
wakil dream:
wakil source:
wakil chore:
```

### Pull Requests

For large ingests or broad note changes, `wakil` should support PR-oriented workflows.

Initial support can use the GitHub CLI rather than a full GitHub API integration.

Example:

```bash id="s0uw5a"
wakil ingest article https://example.com/article --branch --pr
```

The PR body should include:

```text id="xv3gw1"
source summary
files changed
new notes created
links added
memory candidates
review checklist
```

### GitHub Actions

`wakil` should be able to leverage GitHub Actions later for automated functions.

Possible future uses:

```text id="t8phhh"
validate Markdown links
run QMD indexing checks
check frontmatter
generate changed-note summaries
run scheduled dream reports
open automated PRs
publish static knowledge-base views
```

Do not build GitHub Actions integration first. Design commits and CLI commands so that adding Actions later is straightforward.

---

## Ingest Workflow

Ingest is one of the most important workflows.

Supported initial ingest types:

```text id="d177u9"
local Markdown file
whisper zip archive with transcript in JSON format
plain text or SRT transcript
web article URL
Twitter/X URL as metadata-only or fetched text where feasible
pasted text
```

Audio recording support can come later through a transcription tool.

### Ingest Steps

A simple ingest flow:

```text id="02qv2h"
receive source
capture source metadata
extract text
store raw text or source reference with appropriate metadata
summarize source
search QMD for related notes
extract candidate memories
extract candidate relationships
propose note changes
optionally write new notes (e.g. @meeting/yyyy or @media/article)
optionally create branch and commit
```

### Ingest Output

Each ingest should produce:

```text id="z5zlf0"
source record
summary
key points
related existing notes
candidate new notes
candidate memories
candidate relationships
proposed Markdown changes
citations back to source
git diff if files changed
```

The user should be able to accept, reject, or modify proposed changes.

---

## Query Workflow

Query is the everyday workflow.

Example:

```bash id="io92of"
wakil query "How do my notes on claim routing relate to my notes on graph memory?"
```

Query steps:

```text id="xzvwk4"
parse user query
search QMD
search SQLite memory
search source summaries
optionally search git history
select relevant context
ask model for grounded answer
include citations to notes, sources, and memories
suggest follow-up queries
optionally propose new memory or note links
```

Answers should be grounded. If the knowledge base does not support an answer, `wakil` should say so.

---

## Rich CLI Design

The CLI is the product.

Use `Typer` for commands and `Rich` for output.

Command groups:

```bash id="6g26uh"
wakil init
wakil status
wakil ingest
wakil query
wakil search
wakil dream
wakil memory
wakil notes
wakil git
wakil config
```

### Example Commands

```bash id="bdj09v"
wakil init ~/kb

wakil status

wakil search "FNOL routing"

wakil query "What are the strongest connections between my insurance automation notes and graph memory?"

wakil ingest article https://example.com/some-article

wakil ingest transcript ./raw/meeting-transcript.txt --branch

wakil memory list --state candidate

wakil memory promote <memory-id>

wakil dream --recent --write-report

wakil git summary
```

### CLI Output Should Include

```text id="2lg5h1"
readable Markdown
tables
citations
diff previews
confirmation prompts
memory candidates
relationship candidates
suggested next commands
```

Do not hide important state.

---

## Skills

Skills should be Markdown instructions in Claude Skill format <https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview> stored in the project.

Initial skills:

```text id="mg1pdw"
ingest_transcript/SKILL.md
ingest_article/SKILL.md
synthesize_topic/SKILL.md
dream/SKILL.md
promote_memory/SKILL.md
create_concept_note/SKILL.md
```

Example skill:

```markdown id="37sk6m"
---
name: ingest_transcript
description: Ingest a meeting transcript into the knowledge base.
tags:
  - ingest
  - meetings
  - transcript
---

# Ingest Transcript

1. Identify meeting title, date, participants, and major topics.
2. Produce a concise meeting summary.
3. Extract decisions, unresolved questions, and follow-ups.
4. Search the knowledge base for related notes.
5. Propose links to existing notes.
6. Propose candidate memories.
7. Propose one or more durable Markdown notes if useful.
8. Ask for confirmation before writing changes.
```

Skills are not a plugin system. They are simple procedural prompts.

---

## Model Behavior

The model should help with:

```text id="1j3pch"
summarization
topic extraction
relationship discovery
question generation
drafting notes
identifying contradictions
suggesting links
turning raw sources into structured knowledge
```

The model should not control:

```text id="w8m364"
permissions
file boundaries
git operations
database writes
memory promotion without policy
external publishing
```

Application code should remain in control.

---

## Storage Philosophy

Markdown files are the durable knowledge base.

SQLite supports the application.

QMD supports search.

Git provides history and review.

```text id="ju01y0"
Markdown = source of truth
SQLite = local operational memory and index
QMD = knowledge-base search
Git = history, review, and collaboration boundary
LLM = synthesis and reasoning layer
```

This division should stay clear.

---

## MVP Scope

The MVP should include:

```text id="e8pvho"
local CLI
workspace initialization
SQLite database
QMD integration
Markdown note indexing
source records
memory records
relationship records
basic ingest for text and article URLs
query command using QMD + SQLite
dream command as report-only synthesis
git status awareness
optional branch creation for ingests
diff preview before writes
simple commit conventions
Markdown skills
one model provider abstraction
```

The MVP should not include:

```text id="3w2q3w"
Docker
remote runtime
hosted API
web UI
multi-user auth
full GitHub API integration
automatic PR creation unless trivial through gh
audio transcription
Neo4J
background daemon
automated scheduled jobs
multi-agent delegation
```

---

## MVP Acceptance Tests

### Initialize Workspace

Command:

```bash id="4dy91y"
wakil init ~/Projects/kb
```

Expected behavior:

```text id="8p7awh"
creates local config
creates SQLite database
detects git repository
detects QMD if configured
indexes basic Markdown metadata
prints workspace status
```

### Search Knowledge Base

Command:

```bash id="5xnbuc"
wakil search "insurance claims routing"
```

Expected behavior:

```text id="l62y9l"
runs QMD search
shows matching notes
shows paths and snippets
optionally includes related memories
```

### Query Knowledge Base

Command:

```bash id="vq52pl"
wakil query "How do my notes on FNOL relate to my notes on graph memory?"
```

Expected behavior:

```text id="awf37r"
searches QMD
retrieves relevant memories
answers with citations
identifies useful connections
suggests follow-up questions
```

### Ingest Transcript

Command:

```bash id="jqnlz1"
wakil ingest transcript ./raw/meeting.txt --branch
```

Expected behavior:

```text id="c8pnlc"
creates an ingest branch
stores source metadata
summarizes transcript
finds related notes
proposes memory candidates
proposes note changes
shows diff
commits only after confirmation
```

### Dream Recent Work

Command:

```bash id="1s98qh"
wakil dream --recent
```

Expected behavior:

```text id="rpg9ty"
reviews recent memories and notes
searches related knowledge
finds interesting connections
proposes durable memories
proposes note links
writes no changes unless requested
```

---

## Build Phases

### Phase 1: Local CLI and Workspace

Goal: make `wakil` usable against a local Markdown knowledge base.

Build:

```text id="uzjoyx"
Typer CLI
Rich output
workspace config
SQLite setup
git detection
QMD detection
Markdown file indexing
status command
```

Success criterion:

```text id="epj6o7"
wakil can initialize and inspect a local knowledge base.
```

### Phase 2: Search and Query

Goal: make `wakil` useful for asking questions.

Build:

```text id="6gh5z9"
QMD search wrapper
SQLite FTS for memories and sources
query command
model client abstraction
citation formatting
source selection
```

Success criterion:

```text id="7gzo47"
wakil can answer questions using QMD results and local memory records.
```

### Phase 3: Ingest

Goal: turn raw inputs into knowledge-base material.

Build:

```text id="m2c52c"
source model
text ingest
article ingest
summary generation
candidate memories
candidate relationships
related note search
Markdown note proposal
diff preview
```

Success criterion:

```text id="2aqzux"
wakil can ingest a transcript or article and propose useful knowledge-base updates.
```

### Phase 4: Git-Native Changes

Goal: make knowledge-base edits safe and reviewable.

Build:

```text id="y33z7x"
branch creation
commit conventions
dirty-tree checks
diff previews
optional gh-based PR creation
git history summaries
```

Success criterion:

```text id="drcext"
wakil can perform a meaningful ingest or note update on a branch with a clear commit.
```

### Phase 5: Memory Lifecycle

Goal: make memory durable but not noisy.

Build:

```text id="01kna9"
working/candidate/durable/archive states
memory review command
memory promotion
memory fading in retrieval ranking
memory citations
```

Success criterion:

```text id="pppjmf"
wakil can propose, review, promote, and downrank memories.
```

### Phase 6: Dream

Goal: build a useful synthesis mode.

Build:

```text id="7vaplm"
recent memory review
topic-based dream
relationship suggestions
open question extraction
concept note proposals
dream report output
optional write-report mode
```

Success criterion:

```text id="gxjt58"
wakil can produce a non-obvious but grounded synthesis report from existing notes and memories.
```

### Phase 7: Optional Graph Deep Dive

Goal: decide whether Neo4J is worth adding.

Build only if needed:

```text id="m5myab"
export SQLite relationships to graph format
prototype Neo4J import
test multi-hop queries
compare value versus SQLite traversal
```

Success criterion:

```text id="5p2tfa"
Neo4J proves clearly useful for relationship discovery beyond what SQLite and QMD provide.
```

---

## Near-Term Implementation Tasks

A good initial Codex task list:

1. Create the `wakil` Python project with `uv`.
2. Add `Typer`, `Rich`, `Pydantic`, SQLite support, and test tooling.
3. Implement `wakil init`.
4. Create local workspace config.
5. Create SQLite schema for workspace, source, note, memory, relationship, ingest run, and query run.
6. Implement Markdown file discovery and metadata indexing.
7. Detect whether the workspace is a git repository.
8. Detect whether QMD is available and configured.
9. Implement a QMD search wrapper.
10. Implement `wakil status`.
11. Implement `wakil search`.
12. Implement a minimal model client abstraction.
13. Implement `wakil query` using QMD results and memory search.
14. Implement source records for ingests.
15. Implement text transcript ingest.
16. Implement article URL ingest.
17. Implement candidate memory extraction.
18. Implement candidate relationship extraction.
19. Implement diff preview for proposed note changes.
20. Implement memory list, promote, reject, and archive commands.
21. Implement git branch creation for ingests.
22. Implement commit convention helpers.
23. Implement initial `wakil dream --recent`.
24. Add tests with a small fixture Markdown knowledge base.
25. Write docs for QMD integration, memory lifecycle, and git workflow.

---

## First Milestone

The first milestone should be:

> A local CLI tool that initializes a Markdown knowledge-base workspace, indexes notes, searches with QMD, answers grounded questions, ingests text/article sources, proposes memories and note changes, and uses git branches/commits for reviewable modifications.

This is enough to validate the core idea without building unnecessary platform infrastructure.

---

## Design Biases

Prefer:

```text id="9hb2du"
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

```text id="brzeiq"
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

## Examples

### Example Knowledge Base Structure

`wakil` should expect a practical, file-native Markdown knowledge base with a mix of durable notes, raw sources, journals, drafts, and operating instructions.

A representative structure:

```text
.
├── bin
│    ├── qmd-reindex.sh
│    └── qmd-setup.sh
├── companies
├── concepts
├── drafts
├── journal
│    ├── 2025
│    ├── 2026
│    ├── reflections
│    └── undated
├── media
│    └── articles
├── meetings
│    ├── 2025
│    └── 2026
├── people
├── projects
├── sources
│    ├── articles
│    ├── audio
│    ├── clippings
│    ├── messages
│    ├── screenshots
│    ├── transcripts
│    ├── tweets
│    └── videos
├── AGENTS.md
├── README.md
├── RESOLVER.md
└── SCHEMA.md
```

### Directory Expectations

| Path                   | Purpose                                                                                                                                                                                               |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bin/`                 | Local helper scripts for knowledge-base maintenance, such as QMD setup and reindexing. `wakil` may call these when explicitly requested, but should not assume every workspace has identical scripts. |
| `companies/`           | Durable notes about companies, organizations, vendors, employers, prospects, or institutions.                                                                                                         |
| `concepts/`            | Evergreen concept notes. This is likely one of the most important areas for synthesis, linking, and dream outputs.                                                                                    |
| `drafts/`              | Work-in-progress writing, incomplete notes, rough outlines, and material not yet promoted into durable knowledge.                                                                                     |
| `journal/`             | Chronological personal or work notes. Useful for recency-aware queries, reflection, and identifying how ideas evolved over time.                                                                      |
| `journal/YYYY/`        | Year-specific journal entries.                                                                                                                                                                        |
| `journal/reflections/` | Higher-level reflections that may cut across daily or dated entries.                                                                                                                                  |
| `journal/undated/`     | Journal-like material without a clear date.                                                                                                                                                           |
| `media/articles/`      | Curated or durable article notes, summaries, or responses. This is distinct from raw ingested article sources.                                                                                        |
| `meetings/`            | Meeting notes organized by year. These may contain decisions, follow-ups, participants, and project context.                                                                                          |
| `people/`              | Durable notes about people. Useful for resolving names, relationships, meetings, organizations, and collaboration history.                                                                            |
| `projects/`            | Project notes, plans, decisions, status summaries, and related working material.                                                                                                                      |
| `sources/`             | Raw or lightly processed source material brought into the knowledge base.                                                                                                                             |
| `sources/articles/`    | Raw article captures, extracted article text, or source metadata for articles.                                                                                                                        |
| `sources/audio/`       | Audio files or metadata for recordings that may later be transcribed.                                                                                                                                 |
| `sources/clippings/`   | Small captured excerpts from web pages, documents, chats, or other sources.                                                                                                                           |
| `sources/messages/`    | Exported or pasted message threads.                                                                                                                                                                   |
| `sources/screenshots/` | Screenshots or screenshot metadata.                                                                                                                                                                   |
| `sources/transcripts/` | Meeting, call, video, or audio transcripts.                                                                                                                                                           |
| `sources/tweets/`      | Tweet/X thread captures or metadata.                                                                                                                                                                  |
| `sources/videos/`      | Video links, transcripts, captions, or metadata.                                                                                                                                                      |

### How `wakil` Should Use This Structure

`wakil` should not require this exact structure, but it should be able to take advantage of it when present.

On workspace initialization, `wakil` should:

1. Detect the presence of `README.md`, `AGENTS.md`, `SCHEMA.md`, and `RESOLVER.md`.
2. Index their contents as high-priority workspace context.
3. Detect standard directories such as `concepts/`, `people/`, `projects/`, `meetings/`, `journal/`, and `sources/`.
4. Use `SCHEMA.md` to understand metadata conventions before proposing new files.
5. Use `RESOLVER.md` to understand where a file should be stored.
6. Use `AGENTS.md` as operational guidance for agent behavior.
7. Treat `sources/` as raw material and durable note directories such as `concepts/`, `people/`, `projects/`, and `meetings/` as user-facing knowledge artifacts.

The basic distinction should be:

```text
sources/     = raw or lightly processed input material
drafts/      = working material
concepts/    = durable evergreen ideas
people/      = durable person/entity notes
companies/   = durable organization notes
projects/    = durable project context
meetings/    = structured meeting knowledge
journal/     = chronological personal/work context
```

This structure should help `wakil` make better placement, linking, memory, and synthesis decisions without hard-coding the knowledge base too tightly.


### Top-Level Markdown Files

| File          | Purpose                                                                                                                                                                                                                                                                                                                                                                                                         |
| ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `README.md`   | Human-facing overview of the knowledge base: what it contains, how it is organized, and how to work with it. `wakil` should read this early when initializing or answering workspace-level questions.                                                                                                                                                                                                           |
| `AGENTS.md`   | Instructions for AI agents operating in this knowledge base. This should define behavioral rules, writing conventions, safety constraints, preferred workflows, and repository-specific guidance. `wakil` should treat this as high-priority operating context.                                                                                                                                                 |
| `SCHEMA.md`   | Shape and formatting rules for knowledge-base pages. This defines entity types, YAML frontmatter standards, filename conventions, page structure, source attribution, confidence markers, wikilink conventions, and templates. `wakil` should consult this before creating or editing any page so generated notes match the vault’s conventions.                                                                |
| `RESOLVER.md` | Routing rules for where knowledge belongs. This is the authority for deciding whether material should become a person, company, meeting, project, concept, idea, organization note, journal entry, source, archive item, inbox item, or sensitive note. `wakil` should consult this before creating or moving pages, especially when deciding between concepts and projects or when handling sensitive content. |

#### How `wakil` Should Use `SCHEMA.md` and `RESOLVER.md`

`SCHEMA.md` and `RESOLVER.md` have distinct responsibilities:

```text id="h51d6h"
RESOLVER.md = where knowledge goes
SCHEMA.md   = how the resulting page is shaped
```

Before creating, moving, or substantially editing a note, `wakil` should load both files.

`wakil` should use `RESOLVER.md` to decide the canonical destination, for example:

```text id="9s3y32"
people/
companies/
meetings/
sources/
projects/
concepts/
journal/
```

`wakil` should use `SCHEMA.md` to determine the page & metadata format, for example:

```text id="zidshj"
filename slug
frontmatter fields
name vs title usage
page heading
Compiled Truth section
Timeline / Log section
source attribution
confidence markers
wikilink style
attachment placement
```

Important behavior:

1. `wakil` should treat filenames as stable entity identifiers.
2. `wakil` should use lower-case kebab-case filenames.
3. `wakil` should preserve the Compiled Truth / Timeline split on important entity pages.
4. `wakil` should treat the Compiled Truth section as current synthesized state.
5. `wakil` should treat the Timeline / Log section as append-only evidence history.
6. `wakil` should prefer Obsidian-style wikilinks for internal entities.
7. `wakil` should preserve useful existing wikilinks.
8. `wakil` should use root-relative paths rather than relative paths when creating Markdown links.
9. `wakil` should place attachments next to the owning note in a sibling folder with the same slug.
10. `wakil` should warn on sensitive assessments, feedback, compensation, etc.
11. `wakil` should not surface `sensitive/` content in summaries, exports, or shared contexts without explicit instruction.

### Routing Before Shaping

The correct note-creation sequence is:

```text id="tvj5ey"
1. Read RESOLVER.md.
2. Decide where the knowledge belongs.
3. Read SCHEMA.md.
4. Select the right page schema.
5. Create or update the note according to that schema.
6. Use git diff for review before committing.
```

For example:

| Input                                  | Resolver Decision            | Schema Decision                                          |
| -------------------------------------- | ---------------------------- | -------------------------------------------------------- |
| A new person mentioned in a transcript | `people/first-last.md`       | `type: person`, `name: First Last`, no `title:`          |
| A raw transcript                       | `sources/transcripts/`       | `type: source`, `origin: transcript`                     |
| A synthesized meeting note             | `meetings/<date>-topic.md`   | `type: meeting`, `title:`, `date:`, attendees, decisions |
| A reusable mental model                | `concepts/<slug>.md`         | `type: concept`, `name:`, aliases, domain, maturity      |
| A time-bound work effort               | `projects/<scope>/<slug>.md` | `type: project`, `name:`, status, owner, stakeholders    |
| Performance feedback                   | `sensitive/`                 | `type: assessment`, `sensitive: true`                    |

### Implication for `wakil`

`wakil` should not hard-code all routing and schema rules directly into Python.

Instead, it should:

1. detect and index `SCHEMA.md` and `RESOLVER.md`;
2. treat them as high-priority workspace instructions;
3. extract a small amount of structured guidance from them where useful;
4. cite them when explaining why it placed or shaped a note a certain way;
5. fail safely by proposing a destination instead of writing when routing is ambiguous.

The practical rule is:

```text id="s9v1w3"
When routing is unclear, propose.
When schema is clear, conform.
When content is sensitive, protect.
```

---

## Summary

`wakil` should start as a simple, pragmatic, local-first Python CLI for knowledge work over a Markdown knowledge base.

The essential foundation is:

```text id="0cakzv"
Markdown knowledge base
SQLite memory/index
QMD search
git-native workflow
rich CLI
source ingest
query and synthesis
uniform memory model
relationship discovery
dream reports
```

The long-term direction may include richer graph exploration, GitHub Actions, automated review flows, and deeper synthesis. But the first version should remain focused:

> Help one local user make better use of a Markdown knowledge base.
