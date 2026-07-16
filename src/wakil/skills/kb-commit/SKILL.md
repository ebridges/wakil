---
name: kb-commit
description: Commit pending changes in a wakil-managed knowledge base with wakil's kind-prefixed message convention, grouped into reviewable logical change sets. Use after manual edits, after note-revision, or after a schema migrate run that wasn't committed inline.
skill_api: 1
---

# kb-commit

This skill governs commits to a **knowledge-base workspace** wakil manages — not
commits to wakil's own source code. It is a different convention from whatever
governs this dev repo's own history; do not blend the two.

`wakil ingest`, `wakil enrich`, and `wakil schema migrate` each have their own
`--branch`/`--commit` flags and already commit their own output correctly when
those flags are passed. This skill exists for what those flags don't cover:
manual edits to a note, output from a `note-revision` pass, or a
`schema migrate` run made without `--commit`. If a wakil command's own commit
flag is available and appropriate for the change at hand, prefer it over this
skill — this is the fallback path, not the default one.

## When to use

- After hand-editing one or more notes directly (not through `wakil ingest`).
- After a `content-synthesis` or `note-revision` pass leaves the working tree
  dirty.
- After `wakil schema migrate` was run without `--commit`.
- Any other point where the knowledge-base working tree has changes wakil
  itself didn't already commit.

## The commit convention

Every commit message is `wakil <kind>: <description>`, produced by
`commit_message()` in `src/wakil/app/git_service.py`. `kind` must be one of
`COMMIT_PREFIXES`:

```python
COMMIT_PREFIXES = ("ingest", "note", "link", "memory", "dream", "source", "chore")
```

An unrecognized kind is a hard error there (`GitServiceError: unknown commit
kind: ...`), not a warning — treat the list above as closed, don't invent a new
kind for a commit this skill produces.

When this skill constructs a manual `git commit` message by hand (as opposed
to a wakil CLI `--commit` flag doing it), prefix the subject line with the
kind's emoji: `<emoji> wakil <kind>: <description>`. This is a presentation
layer this skill adds on top of `commit_message()`'s output — the function
itself still returns the bare `wakil <kind>: <description>` string, so
anything parsing commit subjects programmatically is unaffected.

| Kind      | Emoji | Use for                                                    |
| --------- | ----- | ----------------------------------------------------------- |
| `source`  | 📥    | raw capture landing in the KB, before processing             |
| `ingest`  | 🧠    | enrichment output — raw capture turned into structured knowledge |
| `note`    | 📝    | manual note edit or note-revision result                     |
| `link`    | 🔗    | cross-reference / back-link addition or repair                |
| `chore`   | 🔧    | tooling, config, schema fixes                                 |
| `memory`  | 💾    | memory-lifecycle change (candidate → active, retirement)      |
| `dream`   | 💭    | reserved: periodic re-synthesis/consolidation pass output     |

What each kind is for, going by how wakil's own code already uses them:

- **📥 `source`** — a raw capture added under `sources/`, before enrichment.
  `commit_ingest`'s docstring: *"'source' for raw captures, 'ingest' for
  enrichment output."*
- **🧠 `ingest`** — enrichment output: the note(s)/entities a `wakil enrich` run
  produced from a source. Under this skill, use it for a manually-applied
  enrichment result you're committing by hand.
- **📝 `note`** — a manual edit to an existing note, or a `note-revision` result:
  a State (Compiled Truth) rewrite, a Timeline append, a prose fix. This is
  the kind you'll reach for most often under this skill.
- **🔗 `link`** — adding or repairing cross-references/back-links without
  otherwise changing a note's content.
- **🔧 `chore`** — tooling, config, schema fixes. This is the kind
  `schema_migrate_service.py` itself uses (see `note-conformance`'s
  "Mechanical fixes" section) — reuse it for the same class of change when
  committing a migrate run by hand.
- **💾 `memory`** — a memory-lifecycle change (candidate → active promotion,
  a memory record's retirement) as described in `docs/memory-model.md`.
- **💭 `dream`** — reserved for output of a periodic re-synthesis/consolidation
  pass once `wakil dream` ships (tracked in `TODO.md`'s `wakil dream`
  entries). Until then, no wakil command produces this kind — use `note` or
  `ingest` for anything currently generated.

There is no general-purpose `wakil commit` CLI command — `commit_message()` is
a library function invoked by `ingest`/`enrich`/`schema migrate`'s own
`--commit` flag and by this skill's manual `git commit` calls alike. Match its
output exactly (`wakil <kind>: <description>`) as the base subject, adding
only the emoji prefix described above, rather than inventing a different
shape for a hand-run commit.

## Procedure

- [ ] Step 1: **Survey.** Run `wakil git summary` for branch, pending-change
      count, and recent commit context. For any single file whose history
      you need to understand before deciding where it belongs, `wakil git
      history <path>` shows its prior commits.

- [ ] Step 2: **Inspect every diff individually before grouping.** `git
      status --porcelain` gives the file list; `git diff --stat` gives shape
      — neither is enough to decide grouping. Open the actual diff (`git
      diff <path>`, or read the first ~30 lines of an untracked file) for
      every changed path before deciding which change set it belongs to. Do
      not `git add -A` and commit as one blob — a note edit and an unrelated
      frontmatter fix picked up in the same sweep is exactly the failure
      mode this step prevents.

- [ ] Step 3: **Group into logical change sets.** One coherent concern per
      commit: a single note's revision, one batch of related back-link
      additions, one schema-migrate type's mechanical fixes. Different notes
      touched for unrelated reasons are different commits even if they
      landed in the same session.

- [ ] Step 4: **Exclude transient, generated, or sensitive files.** Don't
      stage SQLite WAL/journal files, caches, or anything that looks like
      local credentials — if something like that shows up modified, it's a
      sign it doesn't belong in this repo at all, not something to sweep
      into a commit. When in doubt, surface it and ask rather than silently
      staging or silently dropping it.

- [ ] Step 5: **Confirm before committing.** State the proposed change sets
      (message + files) and get explicit approval before running `git
      commit` — this applies even to a single obviously-correct commit.
      Never commit destructive changes (a deletion, a large rewrite) without
      the user having seen the diff first. This mirrors wakil's own
      preview/confirm/apply discipline (`schema_migrate_service.py`'s
      propose → diff → confirm → apply split) — don't hold this skill to a
      lower bar than the code it's filling a gap around.

- [ ] Step 6: **Write the message to describe the knowledge change, not the
      file list.** The subject line is `<emoji> wakil <kind>: <description>`
      in imperative, lowercase-first, no trailing period — the emoji is
      whichever one the commit convention table maps to `kind`, not a
      judgment call. The description names what changed about the knowledge
      base ("resynthesize Acme Corp state after Q3 update-call transcript",
      not "update acme-corp.md"). Add a body when the change isn't
      self-explanatory from the subject alone — what triggered it, what
      source it came from, what was added versus revised.

- [ ] Step 7: **Commit each group, then re-verify.** After each commit, `git
      status --porcelain` again before staging the next group — confirm only
      the intended files moved and nothing unexpected is still sitting
      dirty.

- [ ] Step 8: **Do not push** unless explicitly asked.

## Examples

```
📝 wakil note: resynthesize Acme Corp state from Q3 update call

State section re-derived from the existing page plus the 2026-07-14
transcript; prior distilled history and Timeline entries preserved,
not replaced.
```

```
🔗 wakil link: back-link Jane Doe from the Acme Corp Q3 meeting note
```

```
🔧 wakil chore: apply schema migrate field renames for organization/ notes

end_date -> end-date, start_date -> start-date across 6 files, per
`wakil schema migrate --dry-run --type organization`.
```

```
📥 wakil source: add raw transcript for 2026-07-14 Acme Corp update call
```

## Related skills

- `note-revision` and `content-synthesis` produce the edits this skill
  commits; run this skill after their output, not instead of them.
- `note-conformance` should already have passed on a note before it's
  committed here — a schema-invalid or badly-slugged note is a conformance
  problem, not a commit-message problem.
