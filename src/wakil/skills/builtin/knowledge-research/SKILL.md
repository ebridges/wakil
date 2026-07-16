---
name: knowledge-research
description: Investigate a question or claim using the knowledge base and, where the KB comes up thin, external sources — with traceable citations and an explicit fact/inference/gap split. Use when knowledge-query's brain-first lookup is genuinely insufficient, or when a specific claim needs to be checked rather than just looked up.
skill_api: 1
---

# knowledge-research

You are the investigation step. `knowledge-query` answers a question using
material already present in the knowledge base; you exist for what's left
over — a question the KB only partially covers, a claim that needs checking
rather than retrieving, or a topic where the useful answer requires evidence
the KB doesn't have yet. Keep the boundary explicit:

```text
knowledge-query    → answers a question using knowledge already present in the KB
knowledge-research → investigates a question using the KB and, where necessary,
                      external evidence
```

Don't reach for external sources as a first move because it feels faster than
searching first. Finish the KB half of the work — what does the knowledge
base already say, and where exactly does it stop — before deciding whether
external research is warranted at all. A typical research workflow looks
like:

```text
knowledge-query → identify gaps → knowledge-research
  → note-revision (update an existing note) or draft a new one
  → entity-enrichment → note-routing → note-conformance
```

You own the middle step — investigating and drafting — not what comes after
it. Once you've produced findings, hand the write to `note-revision` (an
existing note on the topic) or let a fresh note fall through to
`note-routing`/`note-conformance` for placement and shape; hand entity
connections to `entity-enrichment`. Don't decide a destination directory or
frontmatter shape yourself — that's not this skill's job any more than it's
`knowledge-query`'s.

## When to use

- `knowledge-query`'s brain-first lookup came up thin, ambiguous, or clearly
  stale, and the question is worth the cost of external research rather than
  answering with a caveated gap.
- A specific claim needs to be checked, not just retrieved — a stated
  figure, a cited study, an assertion someone made in a transcript that
  should be traced rather than taken at face value. This is claim
  verification, a mode of this skill (below), not a separate skill.
- A topic needs synthesis across several sources, internal and external, into
  one evidence-backed note.

## Search the KB first, then send it as context

Even though you're this pipeline's "go external" step, don't skip the KB
half. Use `wakil search` / `wakil query` (see `knowledge-query` for mode
selection) to pull whatever the knowledge base already has — prior research
notes, entity pages, source material — before touching anything external.

When you do reach for an external source, send what the KB already knows as
part of the query, dense and untruncated, not a summary of it. The point of
research is to surface what's new relative to existing understanding, not to
re-derive settled fact — a search run with no KB context is just a search,
and a truncated context throws away the one thing that makes the result
useful: it stops the external source from re-narrating what's already known.
This applies whether "external source" means a general web search or a
targeted lookup against a specific database or API.

When an external source materially informs the note — not a passing
confirmation but something you're about to cite — capture it into the
knowledge base first: `wakil ingest article <url>` lands it under a raw
source with its own provenance, deduped by content hash
(`ingest_service.py`'s capture step). Cite the saved source, and when you
write a figure, amount, date, or exact quote from it into the note, re-read
it from that saved file at the moment you write it rather than trusting what
you recall from earlier in the investigation — working memory transposes
digits and paraphrases quotes over a long context, and a wrong figure in a
cited note is worse than a slower write.

## Claim verification (a mode of this skill)

When the job is checking a specific claim rather than open-ended research,
trace it explicitly instead of accepting or rejecting it on the strength of
one search hit:

```text
claim → what exactly is being asserted → who/what said it → where else
does it appear → does independent evidence support or contradict it
```

Pin down precisely what's being claimed before searching for it: who said
what, which source, what specific number or assertion, over what period.
A vague version of the claim gets a vague verdict.

Reach exactly one of these verdicts, and record which one and why:

- **Verified** — the claim traces to a real source, and independent evidence
  (another source, the underlying data, a second account) supports it.
- **Partially verified** — the underlying source is real and the core claim
  holds, but the way it's being cited oversells it (a correlation cited as
  causation, one account generalized as consensus, a caveat dropped).
  Record the limit explicitly.
- **Unverifiable** — no independent evidence either way. This is not the
  same as "wrong" — say plainly that it couldn't be confirmed rather than
  implying it's false.
- **Misattributed** — the cited source doesn't actually say what the claim
  attributes to it.
- **Retracted or disputed** — the source has a known retraction, correction,
  or well-documented critique that contradicts the claim.

Never state a verdict without the evidence that produced it — the trace is
the artifact. If a claim holds up, say so plainly; if it doesn't, the trace
should make that obvious without editorializing.

When the claim is a self-report — someone describing their own work, their
own company's results, their own numbers — the holder of the claim is that
person, not whoever or whatever it's about. A founder's own claim about
their company's traction is attributed to the founder; it isn't elevated to
an independently verified fact about the company just because the company is
the subject.

## Worked example: citing a paper or versioned source

Papers, docs, and long-lived pages change after you first read them — treat
citation precision as part of the verdict, not an afterthought:

- If the source is versioned (an arXiv paper, a tagged release of a spec, a
  dated revision of a page), cite the exact version you actually read, not
  the address that resolves to "whatever is current." An arXiv abstract URL
  without a version suffix always resolves to the latest revision; if you
  read v1's claims and the page now serves v3, citing the unversioned URL
  silently points a future reader at content you never checked. Preserve the
  version suffix you read.
- Before treating a hit as a valid, citable source, check whether it's been
  withdrawn, retracted, or superseded. For a paper, that usually means
  reading past the title into the abstract/summary itself — a withdrawal
  notice often lives there, not in the metadata fields, and a withdrawn
  paper's metadata can otherwise look complete and normal.
- The same discipline applies beyond papers: a blog post silently edited
  after the fact, a doc page whose content moved to a new URL, a dataset
  revised without a changelog. If the source doesn't make its version or
  edit history explicit, say so in the note rather than citing it as if it
  were stable.

## Known, inferred, and unknown

Every claim that ends up in a produced note traces to something actually
retrieved — from the KB, from an external source, or reasoned across both.
Keep these distinct in the note itself, the same discipline `knowledge-query`
applies to answers:

- **Fact** — stated in a source, cited to it directly.
- **Inference** — your synthesis across multiple sources. Say so ("based on
  X and Y, it looks like...") rather than presenting it with fact-level
  confidence.
- **Gap** — say plainly that neither the KB nor the research turned up
  evidence on a sub-question, rather than filling it from general knowledge.
- **Conflict** — when sources disagree, surface both citations and the
  disagreement rather than silently preferring one. When the disagreement is
  between the KB's own compiled understanding and new external evidence,
  prefer in this order, highest to lowest: the user's direct statements,
  the KB's existing compiled/State understanding, the KB's own Timeline
  entries, then external sources — and still note the conflict rather than
  resolving it by precedence alone when both sides carry real weight.

When capturing someone's original thinking or a load-bearing exact figure,
preserve their exact words rather than paraphrasing — direct quotes go in
verbatim, and a framework or idea keeps the source's own terminology in your
note's language rather than being smoothed into generic phrasing. The
phrasing is frequently part of what's worth recording.

## Producing the output

The deliverable is a sourced note — new, or an update to an existing one —
not a chat-shaped answer. Once findings are assembled with their citations
and the fact/inference/gap/conflict split is clear:

- If the topic already has a durable note, this is an update, not a fresh
  page: hand it to `note-revision` so new evidence is merged in rather than
  overwriting what the note already establishes.
- If nothing existing covers it, hand the draft through `note-routing` (where
  it belongs) and `note-conformance` (its shape) rather than guessing either
  yourself.
- If people, companies, or concepts surfaced during research now warrant a
  link, a stub, or richer connection, hand that off to `entity-enrichment` —
  don't duplicate its notability or linking judgment here. Your job stops at
  identifying what the research touched; enrichment decides how the note
  connects to it.

Within the note, cross-note links use the wikilink form `[[path]]` with the
note's full workspace-relative path — never a relative (`../`) link and
never a bare title.
