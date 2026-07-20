---
title: Skill resolution precedence — first-match-wins, no merging
status: accepted
date: 2026-07-16
audience: wakil design
---

## Context

`wakil` ships built-in skills (`SKILL.md`-bearing directories) and needs to
let a single user override them at the knowledge-base level or the
user-config level without modifying installed files, and without losing
those overrides across upgrades.

`docs/skill-resolution-specification.md` (added in commit `b508290`,
2026-07-15) defines the resolver's search path:

```
1. Roots supplied through WAKIL_SKILL_PATH
2. <kb-root>/skills
3. <user-config>/wakil/skills
4. Built-in skills
```

and states the precedence rule directly (§6, §9.1):

> "The first matching skill directory is authoritative."

The spec's non-goals (§3) explicitly rule out the alternative of merging:

> "merging files across skill directories;
> - skill dependency resolution;
> - automatic drift detection or merging;"

and §4 makes the unit of override the whole directory, not individual files:

> "Once a skill directory is selected, all of its files must come from that
> directory. The resolver must never borrow missing files from a
> lower-precedence implementation of the same skill."

This was implemented in PR #10, "Skill resolution: precedence-ordered
resolver + wakil skills CLI" (merged 2026-07-16, commit range starting at
`f014692`), which added `src/wakil/skills/{models,errors,resolver}.py`
implementing `resolve_skill(name, context)` per the spec, plus `wakil skills
list|which|validate`. The PR description states the mechanism directly:

> "`resolve_skill(name, context)` walks an ordered root list (`WAKIL_SKILL_PATH`
> overrides → `<kb-root>/skills` → user-level `~/.config/wakil/skills` →
> built-in) and returns the first valid match. An invalid winning override
> blocks fallback rather than silently degrading to a lower-precedence
> implementation (spec §9.2)."

A related, initially incomplete part of this decision: the DAG-internal
skill loader used by `wakil enrich` (`wakil.llm.skill_loader.load_skill()`)
did not originally go through this resolver at all — it read
`article`/`text`/`transcript`/`entity-resolve` from a hardcoded flat path.
This meant a kb-local or user-level override was inspectable via `wakil
skills which <name>` but had no effect on actual ingestion behavior. PR #13,
"Wire the skill resolver into wakil enrich's DAG" (merged 2026-07-16,
commit `cab8f23`), closed that gap:

> "Rewrote `skill_loader.load_skill(name, kb_root)` to delegate to
> `resolver.resolve_skill(name, default_context(kb_root))` instead of a
> flat file read."

so that the first-match-wins, no-merging rule became load-bearing
everywhere a skill is loaded, not just in the CLI inspection commands.

The same first-match-wins/no-merging shape was later reused for entity
schemas (`wakil.schema.loader.resolve_schema_roots`, per
`docs/ingestion-refactor-spec.md`) and for page-shape templates, per
session-transcript notes found during triage — this ADR covers the skill
case, which is the original instance of the pattern.

## Decision

Skill resolution uses **first-match-wins precedence with whole-directory
loading and no merging**:

1. Roots are searched in a fixed order: `WAKIL_SKILL_PATH` entries, then
   `<kb-root>/skills`, then the user-level config directory, then built-in
   skills (spec §6).
2. The **first root** whose `<root>/<skill-name>/` directory exists is
   selected. Search stops there (spec §9, §15 — `resolve_skill`).
3. The selected directory is loaded **as a complete unit**: all supporting
   files (`templates/`, `references/`, etc.) resolve relative to that one
   directory. The resolver never reaches into a lower-precedence directory
   to fill in a file missing from the winner (spec §4, §9.3).
4. If the first matching directory is **invalid** (missing `SKILL.md`,
   malformed frontmatter, name/directory mismatch, unsupported `skill_api`),
   resolution **fails outright** rather than silently falling back to the
   next root (spec §9.2). A broken override must be visible and fixed, not
   quietly bypassed.
5. There is no mechanism to merge fields or files across two or more
   matching skill directories at different precedence tiers (spec §3, §18
   invariant 4).
6. This precedence and loading rule is implemented once, in
   `wakil.skills.resolver.resolve_skill`, and consumed by both the
   diagnostic CLI (`wakil skills list/which/validate`) and the DAG-internal
   loader used by `wakil enrich` (`wakil.llm.skill_loader.load_skill`,
   wired in PR #13), so there is one resolution mechanism rather than two.

## Consequences

- Overriding a skill is simple and predictable: drop a full skill directory
  at a higher-precedence root; there is no need to reason about partial
  file-level inheritance or field-level merge semantics.
- A user can create a *broken* override that shadows a working built-in
  skill, and `wakil` will report a hard failure rather than silently running
  the built-in version — this is intentional per spec §9.2 ("a broken
  override should be visible and corrected rather than ignored") but means
  a bad override is a hard stop, not a soft degrade.
- Upgrading `wakil` can never partially merge new built-in content into an
  existing override (spec §13); the override is either fully authoritative
  or not selected at all. Keeping up with upstream changes to a built-in
  skill an override shadows is a manual diff exercise for the user (spec
  §13 defers a "stale override" convenience command).
- Because loading is whole-directory, an override must be a fully
  self-contained skill (its own templates/references/etc.); it cannot
  cheaply extend a built-in skill by supplying only a changed file.
- The rule generalized cleanly: the same kb-local/user/built-in,
  first-match, no-merge shape was reused for entity schema resolution and
  page-shape templates without new design work, per triage notes (see
  Sources) — evidence the pattern earns its keep across more than one
  subsystem.
- **On "why this over merging/layered composition":** the specification
  states the choice and repeats it as an explicit non-goal and an invariant,
  but neither the spec, the PR descriptions, nor the located session
  transcripts record a discussion of alternative designs (e.g. file-level
  layering, config-style deep merge) that were considered and rejected. The
  rationale documented is about the resulting properties (predictability,
  small implementation surface, visible failure on a broken override) —
  not a comparative argument against a specific merging alternative. Stated
  plainly rather than invented: no comparative rationale for
  first-match-wins over merging was found in the sources checked.

## Sources

- `docs/skill-resolution-specification.md`, §2 Goals, §3 Non-goals, §6
  Skill roots and precedence ("The first matching skill directory is
  authoritative."), §4 Skill structure ("Once a skill directory is
  selected, all of its files must come from that directory..."), §9.1
  "First match wins", §9.2 "Invalid overrides block fallback", §9.3
  "Whole-directory selection", §15 Core algorithm (`resolve_skill`), §18
  Invariants (1–4).
- PR #10, "Skill resolution: precedence-ordered resolver + wakil skills
  CLI" (merged 2026-07-16), commit `f014692` "feat(skills): add
  precedence-ordered skill resolver" — implements `resolve_skill` per the
  spec; PR body: "returns the first valid match. An invalid winning
  override blocks fallback rather than silently degrading to a
  lower-precedence implementation (spec §9.2)."
- PR #13, "Wire the skill resolver into wakil enrich's DAG" (merged
  2026-07-16), commit `cab8f23` — rewires
  `wakil.llm.skill_loader.load_skill()` to delegate to
  `resolver.resolve_skill()`/`default_context()` so the same first-match,
  no-merge rule governs `wakil enrich`, not just the CLI inspection
  commands.
- `src/wakil/skills/resolver.py`, module docstring: "an ordered list of
  skill roots ... is normalized, and the first matching, valid skill
  directory wins."
- Transcript `~/.claude/projects/-Users-ebridges-Projects-wakil/9108d770-b024-4e7a-8789-fefeeed40c49.jsonl`
  (approx. 2026-07-15T20:55:13Z): "The first matching skill directory is
  authoritative." and "merging files across skill directories; - skill
  dependency resolution; - automatic drift detection or merging;"
- Transcript `~/.claude/projects/-Users-ebridges-Projects-wakil/3e0a3930-d35f-4ba1-9f24-61e06cf3fde2.jsonl`
  (approx. 2026-07-16): "the resolver and the DAG-internal skill loader
  were genuinely disconnected mechanisms — `wakil skills which` could
  inspect a kb-local override correctly (it only needs one tier to match),
  but `ingest_service.py` never consulted the resolver at all" and "make
  skill_loader.load_skill() delegate to the SAME
  resolver.resolve_skill()/default_context() the CLI already uses."
- Transcript `~/.claude/projects/-Users-ebridges-Projects-wakil/cc7e255d-b19a-449a-82d2-fd2bb127f3f0.jsonl`
  (approx. 2026-07-16T18:13:10Z): "Commit `13ef03e` flattened everything
  into `skills/` directly (no more `builtin/` subdirectory), and `cab8f23`
  rewired `skill_loader.load_skill()` to resolve through `resolver.py`'s
  precedence chain instead of a hardcoded path."
