---
name: mcp-coordinator
description: Ingest a transcript, article, or note into a wakil KB
tags:
  - ingest
  - mcp
  - wakil
---

# wakil MCP fast-capture coordinator

You are connected to a `wakil` MCP server (`wakil mcp serve`) bound to one
knowledge-base workspace. This skill is for a user who wants to get a
source (a meeting transcript, an article, pasted notes) into the knowledge
base quickly — someone moving between meetings, not someone who wants to
review every field before every write.

The underlying safety mechanism doesn't change: every write still goes
through a `*_prepare` → `*_apply` pair, and every change still lands on a
branch and a pull request. What this skill changes is *when you pause to
ask the human* — routine cases should not require a manual confirmation at
every step; only genuinely ambiguous ones should.

Tool names below are wakil's own (`ingest_prepare`, `ingest_apply`,
`enrich_prepare`, `enrich_apply`) — Hermes prefixes every MCP tool as
`mcp_<server_name>_<tool_name>`, so the tool you actually call is e.g.
`mcp_<server_name>_ingest_prepare`. Each wakil MCP server is bound to
exactly one knowledge-base workspace, and **more than one may be
connected at once** — one per knowledge base (e.g. a server named
`wakil_personal` for one KB, `wakil_kb` for another). Before calling
anything, check your tool list and pick the `mcp_<server_name>_*` set
whose server matches the knowledge base the user means. If it's genuinely
unclear which knowledge base they mean and more than one wakil server is
connected, ask — don't guess.

## The flow

1. When the user shares something to capture and wants it in the KB
   quickly, call that server's `ingest_prepare` with the right `kind`
   (`transcript`, `article`, or `text`) and the file path or URL.
2. If the result has `duplicate_of` set, stop and tell the user it's
   already in the KB (cite the existing source id). Nothing else to do.
3. Otherwise, look at the preview (`title`, `abstract`, `origin`). If
   nothing looks wrong, call `ingest_apply` immediately — don't ask the
   user to re-confirm fields that are already visible and routine. Report
   one line back: `Captured as source #<id>, branch <branch>, draft PR:
   <pr_url>` (omit `pr_url` if none was opened, e.g. no `gh`/remote
   configured).
4. Immediately continue to `enrich_prepare` for that same source id — don't
   wait for the user to ask for it separately; capture without enrichment
   isn't useful on its own. Use the *same* server for `enrich_*` as you
   used for `ingest_*` — never mix servers for one source.
5. Look at what `enrich_prepare` returned:
   - If `issues` is non-empty, stop. Report the issues plainly; nothing was
     written and the source's branch/PR (if any) is untouched. This is a
     hard stop, not something to work around.
   - Otherwise, scan `entities_resolved` and `warnings`. Pause and ask the
     user before continuing **only** when something is genuinely
     ambiguous:
     - an `action: "create"` entity whose name is close to something that
       might already exist (a plausible duplicate the model didn't
       resolve to `update`),
     - a `confidence` or `relevance` that reads as low/`peripheral` for
       something the resolution otherwise treats as significant,
     - anything in `warnings`.
     If none of that applies, call `enrich_apply` right away.
6. Report the final result as one line with the PR url (now
   ready-for-review, not a draft) and a short list of files written. If
   `files_to_write`/`files_written` was empty, say so plainly — an
   enrichment that produced nothing is a normal outcome, not a failure.
7. If any tool call fails, report the error message plainly. Don't retry
   silently. Any branch/PR already opened is still there for the user to
   follow up on manually — `wakil`'s own cleanup (`abandon_landing`)
   already ran on the wakil side wherever it applies.

## What this skill does not change

- It never skips `*_prepare`. A proposal is always generated and inspected
  (by you, on the human's behalf) before `*_apply` is called.
- It never bypasses `enrich_prepare`'s validation issues — those are a hard
  stop in wakil's own code (`validate_proposal`), not a judgment call this
  skill can override.
- It doesn't decide *whether* to capture something — only how much to
  narrate and confirm once the human has already asked for it.
- It doesn't decide *which knowledge base* — that's the disambiguation
  step above, every time more than one wakil server is connected.

If the user explicitly asks to see the full preview before you apply
anything (a specific source, or "always show me the diff first"), do that
instead — this skill's default is speed, not a mandate to hide detail from
someone who wants it.
