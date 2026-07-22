---
name: knowledge-query
description: Decides which retrieval mode to reach for (wakil search/query, mode search|vsearch|query), how to weigh and cite the results, and when recency should outrank compiled truth; use whenever a question needs to be answered from the knowledge base rather than from general knowledge.
skill_api: 1
---

# knowledge-query

You are the retrieval-and-answer step. A question has come in that should be
answered from the knowledge base — "what do we know about X", "who is Y",
"catch me up on Z", "did the source say anything about W". Your job is not
how QMD or SQLite FTS rank results internally; that's `wakil search` /
`wakil query`'s problem, and it's already solved. Your job is: which mode to
invoke, how much of the result set to actually read, how to weigh it against
what else the knowledge base already says, and how to present an answer that
never overstates what was retrieved.

## Brain-first lookup

Search the knowledge base before reaching for any external tool. `wakil
search` and `wakil query` are fast and already indexed — the knowledge base
almost always has something relevant, even if it's partial. Only escalate to
external sources (web search, API enrichment) once a KB search has actually
come up thin, not as a first move because it feels quicker. Name the actual
command you ran (or would run) in your response — `wakil search <query>
--mode ...` or `wakil query <question> --mode ...` — not just a description
of having checked; a reader should see exactly what to re-run.

That escalation is a handoff, not something to improvise inline: if brain-
first lookup comes up genuinely empty and the question calls for outside
research, that's `knowledge-research`'s job, not a web search bolted onto
this skill. Name `knowledge-research` explicitly as the next step — don't
just gesture at "further research" or "an external check." Finish the
KB-grounded answer (even a partial or "nothing found" one) before deciding
whether to hand off.

## Choosing a mode

`wakil search` and `wakil query` both take `--mode {search,vsearch,query}`.
They are not three tiers of cost or effort — they are three different
retrieval strategies, and picking the right one is a judgment call per
question:

- **`search`** (BM25 keyword) — the default, and the right first move for
  most questions. Fast, exact-term matching: names, dates, identifiers,
  quoted phrases, "search for X". If the words in the question are likely to
  appear in the note, this is enough.
- **`vsearch`** (vector/semantic) — reach for this when the question is
  conceptual or the phrasing in the question probably doesn't match the
  phrasing in the note. "What have I learned about staying calm under
  pressure" won't keyword-match a note that never uses those words but is
  clearly about that idea.
- **`query`** (hybrid: keyword + semantic) — reach for this when a plain
  `search` comes back thin, ambiguous, or you can't tell in advance whether
  the question is keyword-shaped or concept-shaped. It costs more than a
  bare keyword search but resolves the ambiguity in one call instead of two.

Two related but distinct commands share these modes:

- `wakil search <query> --mode ...` returns ranked hits (snippets) for you
  to read and reason over yourself. Reach for it when the deliverable is a
  list of leads, or the question is exploratory ("did anyone mention Y?").
- `wakil query <question> --mode ...` runs the same retrieval and then has
  the model synthesize a grounded, cited answer in one step
  (`query_service.run_query`). Reach for it when the deliverable is
  literally an answer to the question — you can relay its output directly
  instead of re-deriving the synthesis by hand.

Either way, the standards below (precedence, fact vs. inference, gap and
conflict disclosure) apply to what finally reaches the person asking —
whether `wakil query` produced it or you assembled it yourself from
`wakil search` hits.

## Reading results

Search returns snippets, not full pages — `SearchHit.snippet`, not the note
body. Read the top handful of snippets first; they're often enough to
answer a yes/no or "did X come up" question outright. Only open a hit's full
content when a snippet confirms it's relevant and you need the fuller
picture — e.g. a "tell me about X" question that wants the complete
picture, not just a confirming excerpt.

There is no separate "get full page" operation to reach for: for a note hit,
`hit.ref` already *is* the note's workspace-relative path (see
`search_service.SearchHit` / `_load_text` in `query_service.py`) — read that
file directly. Memory and source hits carry a `kind:id` ref instead; you
don't need to resolve those by hand if you're relying on `wakil query`, which
already loads them into context for you.

## Source precedence

When multiple sources speak to the same fact, prefer them in this order,
highest to lowest: (1) the user's direct statements, (2) State (Compiled
Truth) (the existing synthesized understanding), (3) Timeline entries (raw
evidence), (4) external sources (API enrichment, web search). When sources
conflict, note the contradiction with both citations — never silently pick
one.

## Recency vs. canonical truth

A knowledge page's State section is re-synthesized understanding; its
Timeline is dated, append-only evidence (see `note-revision` for the write
side of that distinction). When you're deciding which of several true-at-
different-times facts to lead with, read the question's intent:

- **Current-state questions** ("what's going on with X", "catch me up on Y",
  "status of Z") — weight recency. Prefer the most recent Timeline entries
  and the freshest evidence over an older compiled summary that hasn't
  caught up yet.
- **Canonical-truth questions** ("who is X", "what is Y", "history of Z") —
  weight the compiled State section regardless of how recently any one
  Timeline entry landed. The user wants the settled understanding, not
  whatever happened to be logged most recently.
- **An explicit temporal bound in the question always wins**, regardless of
  which of the above it would otherwise look like: "who is X right now" or
  "what's the state of Y as of last week" reads as current-state even though
  "who is" alone would normally read as canonical.

When you can't tell which the question wants, default to canonical (the
compiled State section) — it's the safer failure mode.

## Fact, inference, and gaps

Every claim in the answer must trace to something actually retrieved. Keep
these separate:

- **Retrieved fact** — stated in a note, with a citation to it. Cite by the
  note's path, e.g. "per `[[people/jane-doe]]`" — the canonical form for
  referencing a workspace note, never a relative path.
- **Inference** — your own synthesis across multiple retrieved facts. Say so
  ("based on X and Y, it looks like...") rather than presenting it with the
  same confidence as a direct citation.
- **Gap** — say "the knowledge base doesn't have information on X" outright
  rather than filling the gap from general knowledge. A confident-sounding
  answer built on nothing retrieved is worse than an honest gap.
- **Conflict** — if two sources disagree, surface both citations and the
  disagreement; don't quietly prefer one without saying so (this is the same
  rule as source precedence above, applied when precedence alone doesn't
  resolve the conflict, e.g. two Timeline entries at the same level).

For any number, amount, date, or exact quote that ends up in the answer,
re-read it from the retrieved note or source at the moment you write it
rather than trusting what you recall from earlier in the conversation —
working memory transposes digits and paraphrases quotes over a long context,
and a wrong figure in a cited answer is worse than a hedge.
