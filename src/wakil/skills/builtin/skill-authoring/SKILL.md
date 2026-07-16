---
name: skill-authoring
description: Author, edit, override, or promote a SKILL.md file within wakil's skill catalog — MECE scope-check, frontmatter contract, and override/promotion mechanics. Use when creating a new skill, extending an existing one, overriding a built-in, or porting a kb-local skill into the shipped catalog.
skill_api: 1
---

# skill-authoring

This is the meta-skill: it governs how every other `SKILL.md` in this catalog
— and this file itself — gets written, edited, overridden, or retired. It does
not decide what a *note* should say (`content-synthesis`, `note-revision`) or
where a note belongs (`note-routing`); it decides how a *skill file* is
scoped, shaped, and placed. Use it whenever asked to create a skill, extend
one, override a built-in without touching the shipped copy, or lift a
kb-local skill into the shipped catalog.

## When to use

- Asked to add a new skill, or to encode a new repeatable procedure.
- Asked to fix, extend, or correct an existing skill, built-in or override.
- Asked to override a built-in skill for one knowledge base or one user,
  without modifying the shipped copy.
- Asked to promote a kb-local or user-level skill into the shipped catalog
  (or the reverse — narrow a built-in into a kb-local override).

## Procedure

1. **Scan before touching anything.** Check whether an existing skill already
   owns this territory, including skills you haven't read this session — see
   "MECE scan" below.
2. **Decide: extend or create.** Default to extending; only write a new skill
   when nothing existing covers it and the responsibility is genuinely
   skill-shaped — see "Extend vs. create."
3. **If writing a new skill file, satisfy the structural checklist** —
   frontmatter contract, one clear scope, no dangling cross-references — see
   "Structural checklist."
4. **If overriding a built-in** for one workspace or one user, place the
   override correctly and never edit the shipped copy — see
   `references/overriding-a-builtin.md`.
5. **If promoting or porting a skill** across the built-in/kb-local boundary,
   run the genericization pass — see `references/promoting-or-porting.md`.
6. **If something in an existing skill is wrong**, follow the revision
   ladder rather than improvising a one-off workaround — see "When something's
   wrong."

## MECE scan: does this responsibility already have an owner?

The failure mode to guard against: extending whatever skill happens to already
be loaded in context instead of checking whether a different skill actually
owns the territory. A skill that has never been invoked this session is still
authoritative — being loaded does not make a skill the right home for a new
responsibility.

Run `wakil skills list` first. It reports every effective skill name across
all roots — kb-local, user-level, built-in — under normal precedence, not
just what you've already read. Where relevant, also look directly at
`<kb-root>/skills/`, `<user-config>/wakil/skills/`, and this catalog's
`src/wakil/skills/builtin/` on the filesystem, since a skill can exist without
having been invoked yet. Only once no existing skill's stated scope covers
the responsibility should you consider a new one.

Separately, apply a skill-shaped test before writing anything: will this be
invoked more than once, does it carry real procedural judgment worth writing
down (not a one-off script or a single deterministic call), and does its
one-line description actually distinguish it from every sibling a discovery
scan would surface alongside it? If it fails this test, the fix is a note in
conversation, not a new file — regardless of whether an existing skill covers
the territory.

## Extend vs. create

Default to extending. A small set of skills that each own real scope beats
many narrow skills that each own a sliver of one workflow. Concretely,
extending means one of:

- a new subsection in the existing skill's `SKILL.md` body;
- a new file under that skill's `references/` (session-specific detail,
  provider quirks, worked examples) or `templates/` (starter content meant to
  be copied) directory — see any skill that already ships one for the
  pattern; add a `references/` or `templates/` directory to any skill that
  needs one. This skill's own `templates/SKILL.md` is exactly that: the
  blank frontmatter-plus-headings starting point a new skill file is copied
  from;
- a `scripts/` file, if the skill needs one — used sparingly, since wakil's
  skill resolver never executes a skill's supporting files itself (discovery
  and validation don't run skill-provided code); anything under `scripts/` is
  reference material for the agent to read, not something wakil invokes.

Add a one-line pointer from the skill's `SKILL.md` body to any support file
you add — a `references/` or `templates/` file nobody points to is dead
weight.

Create a new skill only when no existing skill's stated scope covers the
responsibility and it clears the skill-shaped test above. Treat the built-in
catalog itself as stable: adding a new *built-in* skill is a decision for
whoever maintains wakil, not something to do mid-task. A new skill for one
knowledge base's own workflow is a kb-local skill by default
(`<kb-root>/skills/<name>/`), not an addition to the shipped set.

## Structural checklist

A finished skill file satisfies all three:

### Frontmatter contract

```yaml
---
name: <skill-name>
description: <one line — what it does and when to use it>
skill_api: 1
---
```

- `name` matches the directory name exactly and is lowercase kebab-case:
  `[a-z][a-z0-9]*(?:-[a-z0-9]+)*`. No path separators, whitespace, uppercase,
  or leading/trailing hyphens.
- `skill_api: 1` is required and, today, the only supported value — the
  resolver rejects anything else outright rather than best-guessing
  compatibility.
- `description` is the whole discovery mechanism. There is no separate
  "triggers" field to also maintain — write one line specific enough that a
  discovery scan won't confuse it with a sibling's.
- `version` is optional and informational only (no dependency solving reads
  it). Add it only if you have a concrete reason to track revisions.

### One clear scope

State in the opening section what the skill does and, as importantly, what it
hands off to a neighbor — every already-written sibling in this catalog does
this (`note-conformance` opens by saying it's "a checking skill, not a
content-generating one"; `ingest-source` tells the reader to stop and hand off
the moment it catches itself making content or entity judgment). A skill whose
opening paragraph can't name what it is *not* responsible for is usually two
skills, or an extension of one that already exists.

### No dangling cross-references

Only name another skill by its exact directory name, and only if it actually
resolves — see "Dangling-reference discipline" below before citing one you
haven't personally confirmed.

## Overriding a built-in

Placing a workspace- or user-level override correctly, without ever editing
the shipped built-in copy, is a whole-directory operation with its own
precedence rules and pre-flight checks — see
`references/overriding-a-builtin.md` for the full mechanics.

## Promoting or porting a skill

Lifting a kb-local or user-level skill into the shipped built-in catalog (or
narrowing a built-in into a kb-local override) is an editorial pass, not a
plain copy — see `references/promoting-or-porting.md` for the checklist.

## When something's wrong

1. **Fix the skill file directly.** Most problems are the skill's own
   instructions being unclear, incomplete, or wrong — edit the `SKILL.md`
   that is actually resolving (built-in, kb-local, or user override,
   whichever wins) and move on.
2. **If the fix reveals a real limitation, say so in the skill's own body.**
   A gap that will recur — a case the procedure doesn't cover, an assumption
   that won't hold for some inputs — gets a plain note in the skill file
   itself, not a silent workaround improvised in the moment. The next agent
   to read the skill should see the same limitation you found.
3. **If the problem is bigger than one skill, escalate to the user rather
   than silently patching around it.** A genuine gap between two skills'
   scopes, or a piece of a workflow nothing owns, is not something to resolve
   by guessing. wakil has no umbrella-skill hierarchy to reroute through, and
   `wakil memory`'s candidate-fact lifecycle is for the knowledge base, not a
   task backlog — it is not a substitute for raising the gap in conversation.
   State plainly what's missing and let the user decide whether it's a new
   skill, a rescoped existing one, or out of scope for now.

## Dangling-reference discipline

Only cross-reference another skill by name if it is one of the 12 in this
catalog: `ingest-source`, `source-ingestion`, `content-synthesis`,
`entity-resolution`, `entity-enrichment`, `note-routing`, `note-conformance`,
`knowledge-query`, `knowledge-research`, `note-revision`, `skill-authoring`,
`kb-commit`. Confirm with `wakil skills list` before citing one you haven't
personally read — a name that sounds right is not the same as a name that
resolves.

Do not reference `article`/`text`/`transcript`/`entity-resolve`
(`src/wakil/skills/builtin/{article,text,transcript,entity-resolve}/SKILL.md`)
as if they were members of *this* 12-skill catalog. They live under the same
`builtin/` root and resolve, override, and appear in `wakil skills list` the
same way — but they're consumed only by `wakil enrich`'s own DAG
(`wakil.llm.skill_loader.load_skill`), never read or followed by an
interactive agent the way the 12 catalog skills are. `ingest-source` is the
place that documents how they fit into the pipeline; don't duplicate or
contradict that here.
