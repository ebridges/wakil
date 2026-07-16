# Promoting or porting a skill

Lifting a kb-local or user-level skill into the shipped built-in catalog (or
narrowing a built-in into a kb-local override) is an editorial pass, not a
plain copy — wakil has no automated harvesting command for this; the
checklist below is the manual discipline that stands in for one:

1. **Strip specific names into placeholders.** Real people, companies, deals,
   or workspace-specific paths in prose or examples become generic
   placeholders — a built-in skill ships to every workspace, while a kb-local
   example is often written against one real knowledge base.
2. **Scrub tool- and workspace-specific references.** Drop mentions of
   anything that isn't part of wakil itself or the open workspace's own
   `RESOLVER.md`/`SCHEMA.md` — a skill written around one person's particular
   setup, or a one-off convention, doesn't belong in the shared catalog.
3. **Document deliberate scope-narrowing.** If the promoted (or narrowed)
   version intentionally does less than the original — drops a
   workspace-specific step, assumes a narrower input shape — say so
   explicitly in the skill's own body, not only in a commit message. A future
   reader of the skill file should never have to guess why a capability is
   missing.
