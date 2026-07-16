# Overriding a built-in

Per the resolution spec, wakil searches skill roots in a fixed order and the
first match wins:

```text
1. WAKIL_SKILL_PATH roots
2. <kb-root>/skills
3. <user-config>/wakil/skills   (XDG_CONFIG_HOME-style)
4. built-in skills
```

To override `note-routing` for one knowledge base only, copy
`src/wakil/skills/note-routing/` in its entirety to
`<kb-root>/skills/note-routing/` and edit the copy — never edit the shipped
built-in directly. Whole-directory selection means that once your kb-local
copy wins resolution, wakil never reaches back into the built-in for a
missing supporting file: bring every `references/`, `templates/`, or
`scripts/` file the skill actually uses along with it, or it silently has
less than the original. The same mechanics apply one root lower, for a
user-level override at `<user-config>/wakil/skills/<name>/`.

Before relying on an override:

- `wakil skills which <name> --verbose` — shows which root actually won, plus
  any shadowed lower-precedence matches, so you can confirm your copy is the
  one being used rather than silently shadowed by an even-higher-precedence
  root.
- `wakil skills validate <name>` — checks the override's frontmatter and
  directory are well-formed. An invalid override blocks fallback to the
  built-in rather than silently using it instead — a broken override fails
  loudly, on purpose, so fix it rather than assume the built-in is quietly
  covering for it.
- `wakil skills list` — the same full-catalog scan used for the MECE step
  above; also the fastest way to confirm your override registered at all.
