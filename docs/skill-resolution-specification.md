# Skill Resolution Specification

## 1. Purpose

This specification defines how `wakil` discovers, selects, validates, and loads
skills.

`wakil` ships with built-in skills and allows a single user to replace them with
knowledge-base-local or user-level implementations. Resolution follows an
ordered search path: the first matching skill wins, and the selected skill
directory is loaded as a complete unit.

The design favors predictable behavior, useful diagnostics, and a small
implementation surface.

---

## 2. Goals

The resolver must:

- allow users to override built-in skills without modifying installed files;
- support knowledge-base-local and user-level skill overrides;
- resolve skills deterministically from a documented precedence order;
- load each skill entirely from one directory;
- fail clearly when the selected skill is invalid;
- make the selected implementation easy to inspect;
- preserve local overrides when `wakil` is upgraded.

---

## 3. Non-goals

The initial implementation does not support:

- remote skill registries or automatic downloads;
- multi-user or organization-level policy;
- inheritance or partial file-level overrides;
- merging files across skill directories;
- skill dependency resolution;
- automatic drift detection or merging;
- filesystem watching or live reload;
- aliases or renamed-skill migration;
- execution of arbitrary skill-provided code.

---

## 4. Skill structure

Skills are forward- but not necessarily reverse-compatible with [Claude Skills](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)

A skill is a directory containing a required `SKILL.md` file:

```text
<skill-root>/
    <skill-name>/
        SKILL.md
```

A skill may include supporting files:

```text
<skill-name>/
    SKILL.md
    templates/
    examples/
    references/
    schemas/
    scripts/
```

Once a skill directory is selected, all of its files must come from that
directory. The resolver must never borrow missing files from a lower-precedence
implementation of the same skill.

---

## 5. Skill names

Skill names must use lowercase kebab case:

```text
meeting-synthesis
article-ingestion
entity-resolution
```

A valid name matches:

```text
[a-z][a-z0-9]*(?:-[a-z0-9]+)*
```

Names containing path separators, whitespace, uppercase letters, `.` path
components, or leading or trailing hyphens are invalid.

The directory name is the canonical skill name.

---

## 6. Skill roots and precedence

The resolver searches these roots in order:

```text
1. Roots supplied through WAKIL_SKILL_PATH
2. <kb-root>/skills
3. <user-config>/wakil/skills
4. Built-in skills
```

The first matching skill directory is authoritative.

For example:

```text
WAKIL_SKILL_PATH=~/experimental-skills
```

produces:

```text
1. ~/experimental-skills
2. <kb-root>/skills
3. <user-config>/wakil/skills
4. <builtin>/skills
```

The built-in root is always added internally. Users do not include it in
`WAKIL_SKILL_PATH`.

### 6.1 Knowledge-base-local skills

Knowledge-base-local skills live at:

```text
<kb-root>/skills/<skill-name>/SKILL.md
```

They apply only to that knowledge base.

### 6.2 User-level skills

User-level skills live under the operating system's application configuration
directory, for example:

```text
~/.config/wakil/skills/<skill-name>/SKILL.md
```

The actual base directory should be determined through the platform's standard
configuration-directory mechanism (e.g. `XDG_CONFIG_HOME`)

### 6.3 Additional roots

`WAKIL_SKILL_PATH` uses the platform's normal path-list separator:

- `:` on Unix-like systems;
- `;` on Windows.

Empty entries are ignored and must not mean the current working directory.

Paths may contain `~` and environment variables. Relative entries are resolved
against the current working directory, although absolute paths are recommended.

---

## 7. Root normalization

Before searching, the resolver must:

1. expand `~` and environment variables;
2. convert paths to absolute normalized paths;
3. remove duplicate roots while preserving the first occurrence.

Missing default roots, such as `<kb-root>/skills`, are ignored.

A path explicitly listed in `WAKIL_SKILL_PATH` that exists but is not a
directory is a configuration error. A missing explicit path should produce a
warning in normal operation and an error in validation commands.

Symbolic links may be followed using normal filesystem behavior. No special
symlink policy is required for the initial single-user implementation.

---

## 8. Skill metadata

`SKILL.md` must begin with YAML frontmatter containing:

```yaml
---
name: meeting-synthesis
description: Synthesizes a meeting transcript into a structured note.
skill_api: 1
---
```

Required fields:

- `name`: must exactly match the skill directory name;
- `skill_api`: identifies the skill contract expected by the file.

Optional fields:

```yaml
version: 2
```

`version` is informational. The resolver does not perform semantic-version
dependency solving.

The initial application supports one explicitly defined `skill_api` version.
A skill requiring another version is invalid.

---

## 9. Resolution behavior

To resolve a skill named `meeting-synthesis`, the resolver:

1. validates the requested name;
2. constructs the ordered skill-root list;
3. checks each root for `meeting-synthesis/`;
4. selects the first matching directory;
5. validates its `SKILL.md`;
6. returns the selected skill and its source.

The normal execution path may stop after selecting and validating the first
match. Diagnostic commands may continue scanning to report shadowed matches.

### 9.1 First match wins

Given:

```text
<kb-root>/skills/meeting-synthesis/SKILL.md
<builtin>/skills/meeting-synthesis/SKILL.md
```

the knowledge-base-local implementation is selected.

### 9.2 Invalid overrides block fallback

If the first matching directory exists but is invalid, resolution fails.

Examples:

- `SKILL.md` is missing;
- frontmatter is malformed;
- `name` does not match the directory;
- `skill_api` is unsupported;
- `SKILL.md` cannot be read.

The resolver must not silently fall back to a lower-precedence implementation.
A broken override should be visible and corrected rather than ignored.

### 9.3 Whole-directory selection

Supporting files are resolved relative to the selected skill directory.

For example:

```text
templates/meeting-note.md
```

means:

```text
<selected-skill-directory>/templates/meeting-note.md
```

It does not resolve relative to the knowledge-base root, current working
directory, or built-in version of the skill.

The resolver does not need to pre-validate every referenced resource. Missing
supporting files may be reported when the skill loader or runtime attempts to
open them.

---

## 10. Resolution result

A successful resolution should return enough information to load the skill and
explain where it came from:

```yaml
name: meeting-synthesis
source: kb-local
root: /Users/example/my-kb/skills
directory: /Users/example/my-kb/skills/meeting-synthesis
manifest: /Users/example/my-kb/skills/meeting-synthesis/SKILL.md
metadata:
  name: meeting-synthesis
  skill_api: 1
  version: 2
```

Required result fields:

- canonical skill name;
- source type;
- selected root;
- skill directory;
- `SKILL.md` path;
- parsed metadata.

The core resolver does not need to calculate content hashes or record every
shadowed candidate.

---

## 11. Errors

The implementation should distinguish these user-visible failure categories:

### Invalid skill name

The requested name violates the naming rules.

### Skill not found

No matching skill directory exists.

The error should list the roots searched.

### Invalid skill directory

The first matching path is not a readable directory or does not contain a
readable `SKILL.md`.

### Invalid skill metadata

Frontmatter is malformed, required fields are missing, or `name` does not match
the directory.

### Unsupported skill API

The skill declares an unsupported `skill_api`.

Error messages should include:

- the requested skill name;
- the relevant path;
- the reason for failure;
- a concise corrective action.

A large hierarchy of resolver-specific exception classes is not required.
These categories may be represented by a small error type with a reason code.

---

## 12. CLI support

The initial implementation should expose three commands.

### 12.1 List skills

```bash
wakil skills list
```

Shows the effective skill names and selected source:

```text
article-ingestion     user
entity-resolution     builtin
meeting-synthesis     kb-local
```

The command scans all roots and applies normal precedence.

### 12.2 Show resolution

```bash
wakil skills which meeting-synthesis
```

Shows the selected path:

```text
/Users/example/my-kb/skills/meeting-synthesis/SKILL.md
```

A verbose form may also show the ordered roots and any shadowed matches:

```bash
wakil skills which meeting-synthesis --verbose
```

### 12.3 Validate skills

```bash
wakil skills validate
wakil skills validate meeting-synthesis
```

Validation checks:

- root accessibility;
- skill directory naming;
- presence and readability of `SKILL.md`;
- frontmatter parsing;
- metadata-name agreement;
- supported `skill_api`.

Commands for copying, diffing, resetting, or rebasing skills are useful
conveniences but are outside the initial resolver scope. Ordinary filesystem
tools are sufficient until those workflows prove cumbersome.

---

## 13. Upgrade behavior

Application upgrades may add, remove, or update built-in skills.

Upgrades must never:

- modify knowledge-base-local skills;
- modify user-level skills;
- merge built-in changes into overrides;
- delete overrides.

A local override continues to take precedence after an upgrade.

The initial implementation does not need to determine whether an override is
stale. Users can compare local and built-in files with standard diff tools or a
future convenience command.

---

## 14. Security boundary

Skills are trusted local content owned by the single user.

The resolver must still:

- validate names before constructing paths;
- prevent path traversal through skill names;
- avoid executing code during discovery or validation;
- load supporting files only from the selected skill directory.

Any future support for executable scripts requires a separate execution and
permission design.

---

## 15. Core algorithm

```python
def resolve_skill(name: str, context: ResolutionContext) -> ResolvedSkill:
    validate_skill_name(name)

    roots = normalize_roots([
        *parse_skill_path(context.environment),
        context.kb_root / "skills",
        context.user_skill_root,
        context.builtin_skill_root,
    ])

    for root in roots:
        skill_dir = root.path / name

        if not skill_dir.exists():
            continue

        skill = load_and_validate_skill(skill_dir, expected_name=name)

        return ResolvedSkill(
            name=name,
            source=root.source,
            root=root.path,
            directory=skill_dir,
            manifest=skill.manifest,
            metadata=skill.metadata,
        )

    raise SkillNotFound(name=name, searched_roots=roots)
```

`load_and_validate_skill` must fail if the selected directory or `SKILL.md` is
invalid. It must not continue searching lower-precedence roots.

---

## 16. Required tests

The initial test suite should cover:

### Resolution

- built-in skill resolves when no override exists;
- user-level skill overrides built-in;
- KB-local skill overrides user-level and built-in;
- `WAKIL_SKILL_PATH` overrides default roots;
- first matching implementation is selected.

### Invalid overrides

- missing `SKILL.md` blocks fallback;
- malformed frontmatter blocks fallback;
- mismatched metadata name blocks fallback;
- unsupported `skill_api` blocks fallback;
- unreadable selected skill blocks fallback.

### Path handling

- missing default roots are ignored;
- duplicate roots are removed;
- `~` and environment variables expand;
- empty path-list entries are ignored;
- invalid skill names are rejected;
- relative supporting files resolve within the selected directory.

### CLI diagnostics

- `skills list` reports the effective source;
- `skills which` reports the selected path;
- `skills validate` identifies invalid roots and skills.

---

## 17. Deferred capabilities

The following should be considered only after real usage demonstrates a need:

- configuration-defined roots in addition to `WAKIL_SKILL_PATH`;
- explicit per-invocation skill-directory overrides;
- skill copy, diff, and reset commands;
- content digests and provenance metadata;
- stale-override detection;
- dependency declarations;
- aliases;
- live reload and cache invalidation;
- protected skill names;
- script execution.

Deferring these keeps the resolver small and avoids committing to behavior that
may never be needed.

---

## 18. Invariants

The implementation must preserve these rules:

1. Skill roots have a deterministic order.
2. The first matching skill directory wins.
3. An invalid winning skill blocks fallback.
4. A skill and all of its resources come from one directory.
5. Built-in skills are the final fallback.
6. Local skills are never modified by application upgrades.
7. The selected source is inspectable.
8. Discovery and validation do not execute skill-provided code.

These invariants define the essential skill-resolution contract.