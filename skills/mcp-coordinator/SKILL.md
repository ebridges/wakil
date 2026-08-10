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
   Same for `collision_source_id`: the computed destination is already
   another source's raw capture, `ingest_apply` will refuse, and there is
   nothing you can do about it from here. Stop and tell the user, citing
   both source ids — the fix is theirs (rename the input, or archive the
   other source).
3. If `collision` is set but `collision_source_id` is not, a file is
   sitting at the destination that no source owns. `ingest_apply` refuses
   this too, and the `--overwrite` that resolves it is CLI-only and
   deliberately not exposed here — overwriting a knowledge-base file with
   no human present is what working-agreement items 11/12 rule out. Report
   the path and stop.
4. Otherwise, look at the preview (`title`, `abstract`, `origin`) **and at
   `warnings`**. Warnings here are informational, not questions: **report
   them and keep going — don't wait for a reply.** They cover a value the
   author's own frontmatter supplied that wakil declined to use, and — the
   one that matters most — a source cut to the model's budget, meaning the
   `title`/`abstract` about to be written into the file's frontmatter
   describe only the part that was read (a plausible abstract of the first
   11% of a two-hour recording looks exactly like a good one). Say so in
   your one-line report so the user can decide later whether to split the
   recording; there is nothing they can do about it at this point that
   doesn't mean discarding the capture, so pausing buys a decision they
   can't act on. Call
   `ingest_apply` — don't ask the user to re-confirm fields
   that are already visible and routine. Report
   one line back: `Captured as source #<id>, branch <branch>, draft PR:
   <pr_url>` (omit `pr_url` if none was opened, e.g. no `gh`/remote
   configured).
5. Immediately continue to `enrich_prepare` for that same source id — don't
   wait for the user to ask for it separately; capture without enrichment
   isn't useful on its own. Use the *same* server for `enrich_*` as you
   used for `ingest_*` — never mix servers for one source.
   `enrich_prepare` runs two real LLM calls (extraction, then entity
   resolution) and can take several minutes on a long source. Tell the
   user up front that this will take a while. If your environment lets
   you run a tool call non-blockingly instead of fully blocking the
   conversation on it, use that for `enrich_prepare` and `enrich_apply` —
   many agent harnesses will do this once they know a call is slow, even
   without being asked explicitly. If yours doesn't, just wait: a long
   `enrich_*` call is expected, not a failure — don't retry it, cancel it,
   or fall back to something else mid-flow because it's taking a while.
6. Look at what `enrich_prepare` returned:
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
     - a warning describing something *ambiguous* — a correction wakil made
       that could have gone another way, a page it couldn't place.
     Truncation warnings are the exception: report them, don't pause on
     them. They describe a budget wakil already applied, the remedy is to
     re-capture a shorter source, and in a workspace whose `RESOLVER.md` is
     oversized the guidance one fires on every source, every run. Pausing
     each time trains the user to wave it through.
     If none of that applies, call `enrich_apply` right away.
7. Report the final result as one line with the PR url (now
   ready-for-review, not a draft) and a short list of files written. If
   `files_to_write`/`files_written` was empty, say so plainly — an
   enrichment that produced nothing is usually a normal outcome, not a
   failure. The one exception is an `enrich_apply` error beginning
   "Nothing was written for this source": that means every page the
   enrichment would have updated lives on a branch that isn't merged into
   the working tree, so the source's material landed nowhere. Report it as
   a failure, name the branches the error lists, and leave the source
   un-enriched. This one needs a human with a shell — the pages have to be
   merged onto *this source's own* branch, which the error names, and
   merging into the default branch won't do it. Nothing was persisted, so
   the enrichment can simply be re-run afterwards; do **not** pass
   `force: true`, which isn't needed and discards the phase checkpoints
   that make the re-run cheap.
8. If any tool call fails, report the error message plainly. Don't retry
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
