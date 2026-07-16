---
name: source-ingestion
description: Normalize non-native source material (meeting exports, PDFs, voice notes, YouTube videos, scanned documents) into wakil's three CLI-native shapes — transcript, text, or article — with a rich --context string, before handing off to `wakil ingest`. Use whenever raw material doesn't already fit one of those three shapes as-is.
skill_api: 1
---

# source-ingestion

`wakil ingest` only knows three shapes of input: a transcript file, a plain
text file, and a web article URL. Most of what actually shows up — a PDF
email export, a voice memo, a YouTube link, a scanned document, a folder of
mixed files — is none of these until someone makes it one. That's this
skill's whole job: turn whatever raw material arrived into one of the three
CLI-native shapes, with a `--context` string rich enough that the model
steps downstream don't have to guess, and then get out of the way.

This is deliberately upstream of judgment about what the material *means*.
Don't extract claims, don't decide what's worth a note, don't resolve
entities here — that's the ingestion DAG's job once your normalized file
reaches it.

## Where this sits in the pipeline

```text
raw material (any shape)
        │
        │  source-ingestion — THIS skill: normalize to transcript/text/article
        ▼
wakil ingest transcript|text|article    (capture: deterministic, no model)
        │
        ▼
wakil enrich <source-id>                (DAG: extraction → entity resolution → apply)
        │
        ├─ DAG node 1: transcript, text, or article skill — extraction
        │  judgment
        └─ DAG node 2: entity-resolve skill — always invoked
```

`article`/`text`/`transcript`/`entity-resolve` live under `src/wakil/skills/`
alongside this 12-skill catalog and are resolved, overridden, and validated
the same way (`wakil skills list/which/validate` show them too) — but they're
invoked automatically by `wakil enrich`
(`src/wakil/app/ingest_service.py`), not part of *this* 12-skill catalog, and
this skill does not re-derive their judgment. Once your normalized file is
captured, their extraction and entity-resolution judgment takes over; nothing
here should anticipate or duplicate it.

Deciding *whether* a given piece of raw material needs this
pre-normalization step at all, and actually invoking `wakil ingest` /
`wakil enrich`, is `ingest-source`'s call. This skill's job ends the moment
material exists as a transcript/text/article-shaped file (or a directly
fetchable article URL) with a `--context` string ready to hand off.

If the material is already fully normalized and you're deciding what to
*say* about it — which claims matter, what to quote, how it connects to
existing notes — that's `content-synthesis`'s territory, not this skill's.

## The three CLI-native shapes, and everything else

- **`wakil ingest transcript <file>`** — a meeting or call transcript
  (`.txt`, `.md`, `.srt`). Multi-party, dated, attributable to speakers.
- **`wakil ingest text <file>`** — a plain text file, pasted note, or
  clipping. Solo narration, a fragment, anything that isn't a dialogue
  between named parties.
- **`wakil ingest article <url>`** — a web article URL, fetched and
  extracted directly. Only for content already reachable as a URL.

Every one of the three accepts `--context` / `-C`: a few lines about the
source (attendees, company, purpose) that feeds directly into the
downstream extraction judgment — a transcript's DAG node 1 explicitly uses
it to resolve first names and pronouns to full names. Building a strong
`--context` string is as much a part of normalization as producing the file
itself; a well-shaped transcript with a thin or missing context string
still leaves the extraction step guessing.

**Anything that isn't already one of these three shapes must be
pre-normalized into one before `wakil ingest` runs.** A PDF, a voice memo,
a scanned document, a YouTube video — none of these can be handed to
`wakil ingest` directly; wakil has no binary or audio/video ingestion path.
Extract the material to text (or, for audio/video, to a transcript of what
was said), decide honestly whether the result reads as a transcript
(multiple speakers, a call or meeting) or as text (a monologue, a document,
a clipping), write it to a plain-text file, and compose the `--context`
string before invoking `wakil ingest`. See `references/source-types.md` for
the per-source-type judgment — what to extract, how to classify it, and
where each legacy format's genuine hard-won judgment carries over.

## Exact-phrasing preservation

When normalizing captures someone's original words — a transcript, a voice
note, a quoted passage — use their exact words. Don't paraphrase, don't
clean up grammar, don't smooth over hesitations or filler in a spoken
transcript. The language is the insight, and normalization is exactly the
step where that phrasing is easiest to lose: a transcription pass or a
text-extraction tool that "cleans up" the output as a side effect quietly
destroys the thing worth capturing. Write the normalized file with the
source's own wording intact; let paraphrase happen later, if at all, during
synthesis — never during normalization.

Back-linking every mention of a person or company that already has a page
back to wherever it was mentioned is a related but separate discipline —
an unlinked mention is a broken knowledge base. That linking happens
downstream, during entity resolution and enrichment, once the normalized
file has been captured; this skill's contribution is making sure the
`--context` string names the people and companies involved clearly enough
that the downstream step can actually find and link them.

## Extraction integrity

Never trust working memory for a figure, amount, date, or exact phrase
that needs to go into the normalized file or its `--context` string.
Re-read it from the extracted source at the moment of writing — a PDF's
`pdftotext` output, a fetched transcript, an OCR pass — rather than
recalling it from earlier in the conversation. This is exactly where batch
normalization goes wrong: several PDFs or transcripts get extracted in one
session, and a number or name from one bleeds into the write-up of another
because it was carried in memory instead of re-read from the file that was
actually saved to disk.

## Test 3-5 before bulk

Before running any bulk normalization — a folder of PDFs, an email export
with dozens of messages, a batch of voice memos — normalize and ingest a
small sample (3-5 items) first and inspect the resulting captures by hand
before processing the rest. A bug in the extraction or classification logic
that corrupts 3 files is a quick fix; the same bug silently applied across
300 is a rebuild. Wakil's own `wakil schema migrate` embodies this
discipline structurally — `schema_migrate_service.py` only ever proposes a
migration and diffs it before anything is rewritten, and re-verifies each
file is unchanged since planning before it touches disk. Bulk normalization
has no equivalent built-in safety net, so apply the same discipline by
hand: sample, inspect, then proceed.

## Handing off

Once material is normalized, hand off in order:

- [ ] Step 1: Normalize the material into one of the three CLI-native
      shapes (`transcript`/`text`/`article`) with a rich `--context` string,
      as described above.
- [ ] Step 2: Run `wakil ingest transcript|text|article` with the
      `--context` string you built. That capture step is deterministic — no
      model runs, nothing is interpreted — so a thin context string here is
      a problem you can't fix later by hoping the extraction step
      compensates.
- [ ] Step 3: Let `wakil enrich <source-id>` run the extraction and
      entity-resolution DAG automatically; you do not need to (and should
      not) pre-decide what the material means or which entities it
      touches.
- [ ] Step 4: Hand off further judgment as needed. If you're deciding
      whether normalization was even the right call, or orchestrating the
      ingest/enrich sequence itself (branches, commits, PRs), that's
      `ingest-source`'s territory. If the resulting note needs a home
      beyond what `wakil enrich` proposes, or needs merging into a note
      that already exists, that's `note-routing` and `note-revision`,
      respectively — not this skill.
