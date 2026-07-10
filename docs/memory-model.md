---
title: Modeling Memory in a Knowledge-Base Agent
status: draft
audience: wakil design
---

# Modeling Memory in a Knowledge-Base Agent

A knowledge base that only accumulates degrades. Every note added raises the
odds that the next search returns near-duplicates, that a stale fact outranks
the correction that replaced it, and that the one useful record is buried under
forty redundant ones. Human memory faces the same three problems — things fade,
things resurface, and similar things interfere — and it solves them with
mechanisms that map cleanly onto design choices for an agent like wakil.

This paper works through those mechanisms one at a time, pairs each with its
agentic analog, and closes with a concrete model for wakil that stays inside the
project's constraints: Markdown as the source of truth, SQLite as the
operational index, a candidate-to-active memory lifecycle, and human review for
anything that changes durable content.

The central claim is one distinction, borrowed from the memory literature and
worth stating up front: **how retrievable a memory is** and **whether it is
stored at all** are different variables. Human forgetting is mostly the first,
not the second. Copy that split and you get graceful forgetting and surprise
recall from the same machinery, without ever destroying data.

## Fading

Four distinct things get called "forgetting," and they call for different
responses.

**Decay from disuse.** Traces weaken over time when not accessed. The
forgetting curve is roughly exponential — a steep initial drop, then a long
tail. Pure time-based decay is contested as a sole mechanism; much of what looks
like decay is interference wearing a disguise. The agentic analog is a
recency-and-frequency weight on each record: a score that falls with time and
rises with use. Cheap to compute, and it needs no background process if it's
derived from a last-accessed timestamp on read.

**Interference.** Often the larger driver. New information competes with old,
old competes with new, and similar traces blur together. This is the "which
parking spot today" failure — two hundred near-identical memories overwrite each
other into mush. In a knowledge base this is the dominant failure mode, not a
footnote, so it gets its own section below.

**Retrieval failure.** A large fraction of forgetting is not lost storage. The
trace is intact; the cue that reaches it is missing or too weak. The information
is there and the index is broken. This is the single most useful idea to carry
into the design: down-weight the index, keep the data.

**Consolidation failure.** Some memories were never durable. Low attention, no
salience, no rehearsal — they didn't fade, they never solidified. The analog is
a record ingested but never cited, never linked, never confirmed. It should be
easy to let those lapse.

Set against these is one more, which is a feature rather than a fault.

**Adaptive forgetting.** The brain prunes on purpose. Forgetting discards
outdated, low-value information so the signal isn't drowned by noise. It's a
compression strategy. An agent that treats forgetting only as loss will keep
everything and search will rot; an agent that treats controlled forgetting as
maintenance stays useful as it grows.

## Resurfacing

The other half of the question: why do forgotten things come back, sometimes
suddenly. The answers all follow from retrieval failure being reversible.

**Cue encounter.** The dominant reason. A context, phrase, or associated concept
happens to match the missing retrieval path, and the trace lights up. The memory
was there the whole time; you found the door. The analog is direct: a query
whose terms overlap a dormant record's associations should be able to surface
it, even when that record sits below the normal ranking threshold.

**Spreading activation.** Thinking about a concept partially activates its
neighbors. Enough accumulated partial activation crosses a threshold and the
memory arrives — which is why "thinking around it" works and why the answer
turns up minutes after you stopped trying. The analog is retrieval that isn't a
flat lookup: activating one record lends weight to linked records, so a
related-but-not-matching note can still come up.

**Release from interference.** Stepping away lets competing traces settle so the
target is no longer crowded out. The analog is that a record suppressed by a
dense cluster of similar records becomes reachable once the cluster is thinned
or once a more specific cue distinguishes it.

**Reconsolidation.** Each recall reopens a memory for editing. A retrieved
memory becomes briefly more accessible and can be rewritten, strengthened, or
corrupted before it settles again. This is the subtle one, and it's where a
naive design goes wrong, so it gets its own section too.

## Reconsolidation, in detail

The obvious implementation — strengthen a memory every time it appears in a
result — is wrong, and not only for the concurrency reasons covered below.
Semantic retrieval returns many candidates, most of them noise for any given
query. If mere candidacy strengthens a record, the records that win are the ones
that keep matching, not the ones that prove useful. Retrieval popularity is not
value. Left unchecked it's a rich-get-richer runaway.

The fix starts by splitting one variable into three.

- **Activation** — a fast-decaying "recently touched" signal. Bump it freely on
  retrieval; it's cheap, noisy, and self-erasing. This is the real analog of the
  temporary post-recall accessibility, and nothing durable should hang off it.
- **Strength** — the slow, valuable score. It updates only on a confirmation
  signal, with diminishing returns, and decays on its own.
- **Content** — the text of the memory. Rewritten only on a meaningful event,
  never on every touch.

With that split, the interesting property of biological reconsolidation
survives: recall isn't monotonic strengthening. It opens a window in which the
memory can go up, down, or sideways depending on what happens in the window. The
driver is prediction error, not the access itself. A memory that keeps being
confirmed generates less and less surprise, so its updates shrink toward nothing
on their own — the ceiling is emergent, not imposed. A memory that's contradicted
gets rewritten or demoted. New associated information triggers a merge or a
re-summary.

As a scalar, a saturating update captures the same bounded behavior:

```text
strength += alpha * (ceiling - strength) * signal   # confirmation, saturating
strength -= beta  * strength                          # decay over time
```

The increment shrinks as strength rises, decay pulls the other way, and the
record settles at an equilibrium that reflects its genuine confirmed-use rate.
Bounded, not runaway. The equilibrium level is the useful information.

That leaves the load-bearing question: what is the confirmation signal? Mere
candidacy is the wrong answer. The right ones are downstream evidence that the
memory did work — it was cited in an answer, the answer was accepted, or it was
confirmed or contradicted by newer authoritative content. Without one of those,
prediction error is a metaphor with nothing to measure.

## Interference, in detail

Everything above is per-record: strength, decay, and reconsolidation each look
at one memory in isolation. Interference is relational — a property of pairs and
clusters. No per-record mechanism produces it. It has to be added as something
that reasons about neighbors, and it shows up in three places that map onto the
three human forms.

**Write time — resolve it structurally.** When a new memory arrives, compare it
against what's already stored. The near-neighbors fall into four buckets, and
the right action differs for each:

- **Duplicate** — merge, raise a support count, don't store a second copy.
- **Refinement** — same topic with more detail; re-summarize into one richer
  record.
- **Contradiction** — stale versus fresh; this is not a merge. Supersede by
  recency and version the old one out.
- **Coincidental similarity** — close in embedding space, genuinely different
  facts; keep both, and link them so retrieval can tell them apart.

The naive rule `similar -> merge` collapses all four into the first two and
quietly corrupts the base. The real operation is a classifier plus an action,
not a similarity threshold.

**Read time — interference as crowding.** Top-k scoring judges each record
against the query independently, so a cluster of near-duplicates sweeps the top
slots and starves diverse-but-relevant results. That is interference: redundant
traces drowning the signal. The standard fix is diversity-aware selection, where
each pick penalizes later picks that resemble it:

```text
score(item) = lambda * sim(query, item)
            - (1 - lambda) * max sim(item, already_selected)
```

The framing is lateral inhibition — selecting a record suppresses its neighbors
for that query. It lets you carry a cluster's worth (one representative plus its
support count) without letting the cluster crowd the results.

**Confirmation time — retrieval-induced forgetting.** In people, recalling one
item suppresses its competitors; retrieving A makes B harder to reach later. The
analog reuses the confirmation signal from reconsolidation and gives it a second
job. When one record wins — gets cited, drives an accepted answer — raise it and
lower its uncited near-duplicates:

```text
strength[w] += alpha * (ceiling - strength[w]) * signal        # winner up
strength[d] -= gamma * sim(w, d) * signal   for d != w         # crowd down
```

Over many queries a redundant cluster collapses toward its best representative,
with no one deciding to delete the rest. They decay below threshold from
repeated non-selection. That is how the biology thins redundancy, and it's
cheap.

Two failure modes to design against, both from treating similar records as
interchangeable. First, fifty copies of a fact are not fifty votes; track
support count and independent-source count separately, and weight by provenance,
not raw multiplicity. Second, over-merging destroys real distinctions — "4B on
Tuesdays" and "4C on Thursdays" are close in embedding space and are different
facts. Here copying human behavior, which famously smears similar episodes
together, is the bug. Bias merges conservative, require more than embedding
proximity before collapsing two records, and keep merges reversible.

## Where this meets wakil's principles

One tension has to be named directly. The clean engineering answer to
reconsolidation and interference is an asynchronous background worker that
rewrites and merges memories on its own — single-writer per record, debounced,
calling a model to re-summarize. That design is correct in the abstract and
wrong for wakil. The project's stated biases rule out hidden background
behavior and automatic rewriting of user knowledge without review. Silent
reconsolidation is exactly the thing wakil exists not to do.

So the proposal below keeps the mechanisms and drops the autonomy. Scalars that
are cheap, deterministic, and reversible run on their own. Anything that changes
durable Markdown is a proposal surfaced for review, expressed as a diff, gated
on confirmation or the existing candidate-to-active promotion flow. Cost: the
base won't self-maintain while you sleep, and some cleanup waits on a human.
Why it's right anyway: in a personal knowledge base the whole value is trust in
the content, and a wrong silent merge costs more than a slow one.

## Proposal: a concrete model for wakil

Memories already live in SQLite as `candidate` records for review and promotion.
Extend that record, add a lifecycle, and attach the mechanisms above to events
wakil already has — ingest, index, search, and query.

**State on each memory record.** Keep the Markdown notes as the source of truth
and hold the dynamics in SQLite, where they belong as operational state:

- `strength` — the durable score, a float, default low.
- `activation` — fast-decaying recency signal; derived, not stored long.
- `last_accessed`, `access_count`, `cited_count` — the raw inputs to both above.
- `support_count`, `source_count` — multiplicity versus independent provenance,
  kept apart on purpose.
- `state` — one of `candidate`, `active`, `dormant`, `archived`.
- `supersedes` / `superseded_by` — contradiction lineage, so nothing is
  destroyed by a correction.

**Lifecycle.** `candidate` on ingest, as today. Promotion to `active` on human
confirmation or first citation in an accepted answer. Demotion to `dormant` when
strength decays past a floor — still stored, still searchable, just below the
surfacing threshold, which is the retrieval-failure analog. `archived` only by
explicit action, and even then recoverable through git history. No state
transition deletes a Markdown file.

**Decay, computed lazily.** No background job. Derive the current score from
`last_accessed` and `access_count` at read time, or fold it in during the
explicit `wakil index` pass. Deterministic, cheap, and it respects the ban on
hidden behavior because it only runs when the user runs a command.

**Interference at ingest.** wakil's ingest already finds related notes and shows
a preview before writing. Extend that step with the four-way classifier —
duplicate, refinement, contradiction, coincidence — and put its verdict in the
preview. The user confirms a merge, a supersede, or "keep both, link them." The
model proposes; the human decides; the result is a reviewable diff. This is the
same shape as the current confirm-before-write flow, with a sharper suggestion
inside it.

**Diversity at search.** Apply the lateral-inhibition penalty when assembling
results, so a cluster of near-duplicates contributes one representative plus its
support count instead of ten rows. This changes ranking only; it touches no
stored content and needs no review.

**Confirmation from query.** `query` already records runs. Record which memories
were actually cited in an accepted answer, and treat that as the confirmation
signal: raise the cited record's strength on the saturating curve, and apply the
small retrieval-induced decrement to its uncited near-duplicates. Both are scalar
updates to SQLite, reversible and invisible to the Markdown.

**Reconsolidation as a reviewable proposal.** When a citation or a contradiction
suggests a memory's content should change, wakil does not rewrite it. It emits a
reconsolidation candidate — a proposed edit or merge — into the same review
queue ingest uses, as a git diff. Approved, it lands as a normal reviewable
commit; declined, nothing changes. The labile window becomes a pull request,
not a silent edit.

The one distinction to hold onto is the same one from the top: separate how
retrievable a memory is from whether it's stored. Retrievability is dynamic,
scalar, automatic, and reversible. Storage and content are Markdown, changed
only through review. That single split gives wakil human-like forgetting and
resurfacing while keeping every promise in `CLAUDE.md`.

## Summary map

| Human mechanism | Agentic analog | wakil mechanism |
| --- | --- | --- |
| Decay from disuse | Recency/frequency weight | `strength` from `last_accessed`, computed on read/index |
| Interference | Similarity competition | Four-way classifier at ingest; diversity penalty at search |
| Retrieval failure | Low index weight, data intact | `dormant` state — below threshold, still searchable |
| Consolidation failure | Never-cited record lapses | `candidate` that's never promoted |
| Adaptive forgetting | Controlled pruning | Demotion and archival, recoverable via git |
| Cue encounter | Term/association overlap | Query surfaces dormant records on match |
| Spreading activation | Linked-record boost | Activation lent across wikilinks |
| Reconsolidation | Prediction-error update | Scalar strength update; content changes as reviewed diffs |
| Retrieval-induced forgetting | Winner up, crowd down | Citation raises one, decrements near-duplicates |
