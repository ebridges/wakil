# Per-source-type normalization judgment

Dispatch table for `source-ingestion`. Each section covers one non-native
source shape: what genuine judgment from prior experience carries over,
what to extract, and which of wakil's three CLI-native shapes
(`transcript` / `text` / `article`) the result becomes. None of this
decides what the material *means* — that's the ingestion DAG's job once
`wakil ingest` runs.

## Contents

- [Meeting transcripts](#meeting-transcripts-from-exports-pdfs-or-raw-capture-files)
- [PDFs](#pdfs-documents-contracts-email-thread-exports)
- [Voice notes](#voice-notes)
- [YouTube videos](#youtube-videos)
- [OCR / scanned documents](#ocr--scanned-documents)
- [Deferred: media ingest and archive crawling](#deferred-media-ingest-and-archive-crawling)

## Meeting transcripts (from exports, PDFs, or raw capture files)

Destination shape: `wakil ingest transcript`.

- **Attendee identification belongs in `--context`, not in manual page
  creation.** `wakil enrich`'s entity-resolution DAG step (the
  `entity-resolve` skill) is always invoked and will create or update
  attendee pages itself — don't do that by hand here. What normalization
  contributes is making sure the extraction step doesn't have to guess: list
  attendees with full names, roles, and company in the `--context` string.
  The `transcript` skill explicitly uses this context to resolve first names
  and pronouns to full names — a transcript normalized without it forces the
  extraction step to guess who "Jane" or "he" refers to.
- **Verify proper-noun spelling before it goes into the context string or
  the file.** Auto-generated transcripts and hand-typed filenames
  frequently misspell names and company names. Cross-check against an
  authoritative source when one is available (an email thread, the
  company's own site, a press release or LinkedIn URL) before it becomes
  the spelling that gets propagated into an entity's canonical name
  downstream — a typo caught here is a five-second fix; a typo that
  becomes a page name is a rename later.
- **Meeting date is not capture date.** wakil's own `source` schema
  distinguishes them for transcript-origin sources: `captured` (when the
  file was captured) and `meeting_date` (when the meeting actually
  happened) are separate fields (`src/wakil/schema/entities/source.yaml`,
  `origins.transcript`). A filename or file-system timestamp is a capture
  artifact, not evidence of when the meeting occurred — if the transcript
  content or surrounding context establishes a different date, put the
  real meeting date in `--context` and let it flow into `meeting_date`
  rather than trusting the filename.
- **Prep-doc reconciliation is real, workspace-owned judgment.** If the
  workspace keeps a prep note ahead of meetings (its own convention, not a
  wakil field), and one exists for this meeting, read it before
  normalizing: pull its planned questions and flagged topics into the
  `--context` string so the extraction step can note what got resolved
  and what's still open, and plan to cross-link the meeting note back to
  the prep note once both exist. Follow whatever the workspace's own
  `RESOLVER.md`/`SCHEMA.md` say a prep note looks like — don't invent a
  frontmatter shape for it.
- **A header-only or partial transcript is a valid capture, not an
  error.** Normalize and ingest what exists, note in `--context` that the
  capture is partial, and re-run `wakil ingest`/`wakil enrich` once the
  full transcript is available rather than waiting to ingest anything.
- **Flag suspicious numeric claims rather than silently normalizing
  them.** Conversational settings are where equity percentages get
  confused with basis points, round sizes get misquoted, and headcounts
  get rounded into "hired" when the source actually meant "interviewed."
  When something reads as an obvious unit or magnitude error, note both
  the verbatim claim and the likely correction in `--context` — don't
  quietly fix it and don't quietly drop it.

## PDFs (documents, contracts, email thread exports)

Destination shape: `wakil ingest text` (or `transcript`, if the PDF is
itself a meeting-transcript export — see above).

- **Extract text before wakil ever sees the file.** wakil has no binary
  ingestion path — a `.pdf` path handed to `wakil ingest` is not something
  it can parse. Extract to plain text first (a layout-preserving mode when
  the document has tables or columns matters — a naive extraction can
  scramble reading order), and review the extracted text before writing it
  to the file `wakil ingest` will read.
- **Classify before normalizing.** A PDF might be an email thread, a
  meeting transcript export, a contract, a resume, or a clipped article.
  The classification decides the destination shape and which entities the
  `--context` string should call out — an email thread's participants are
  its context in the same way a meeting's attendees are.
- **De-duplicate quoted and forwarded content in email exports.** Email
  thread PDFs routinely contain the same earlier message quoted or
  forwarded multiple times as the thread grows. Strip the duplication
  before writing the normalized file — otherwise the same claim gets
  captured (and later extracted) several times over, and a downstream
  read overweights it.
- **Never pass the PDF path itself onward.** Not to `wakil ingest`, not
  embedded as a reference in the normalized text file in place of its
  content. The extracted text is the source of truth `wakil ingest`
  captures; the original PDF is provenance, not input.

## Voice notes

Destination shape: almost always `wakil ingest text` — a voice note is
solo narration, not a dialogue between named parties, so it rarely
qualifies as a `transcript`. Use `transcript` only if the recording is
itself a multi-party call.

- **Transcribe verbatim.** This is the sharpest case of exact-phrasing
  preservation: hesitations, filler words, unfinished sentences, and rough
  phrasing are exactly what makes a voice note worth capturing over a
  cleaned-up written note. Don't let a transcription tool's "clean mode"
  smooth any of it out.
- **Don't pre-decide the subject-matter classification here.** Whether
  this note is best understood as a new idea, a reflection, an update
  about a person, or something else is a routing/synthesis judgment made
  after capture — by `note-routing` once a note is proposed — not
  something to resolve while normalizing the raw transcript. What
  normalization contributes instead: a `--context` string naming the
  channel, date, and — when the note is clearly about a specific person or
  company — who or what it concerns, so the downstream steps have enough
  to work with.

## YouTube videos

Destination shape: `wakil ingest text` for a monologue or single-narrator
video; `wakil ingest transcript` if the video is substantially an
interview or multi-party conversation.

- **Fetch the transcript, don't summarize the video description.** A
  video's real content is its transcript (captions or an automated
  transcription), not its title or blurb.
- **If transcripts are disabled or unavailable, say so rather than
  fabricating a summary from the title and description.** A missing
  transcript is a dead end for this skill, not something to paper over
  with a guess at what the video probably covers.
- **Put the video title, channel/speaker, and URL in `--context`.** That's
  the equivalent of a transcript's attendee list — it's what lets the
  downstream extraction step attribute claims correctly.
- **Chunking a very long video's transcript for readability is a capture
  concern, not a synthesis one; how much of a long transcript is worth
  deep synthesis is `content-synthesis`'s long-document strategy, not
  this skill's.** Normalize the whole fetched transcript into the file;
  don't pre-trim it down to what seems most relevant.

## OCR / scanned documents

Destination shape: `wakil ingest text`.

- **Try direct text extraction first.** Most PDFs and scanned exports
  that look "scanned" still carry an embedded text layer; a plain
  extraction pass is faster and more faithful than OCR whenever it works.
- **Fall back to OCR only when direct extraction is empty or clearly
  garbled**, not as a default first step — OCR is slower, and its output
  needs the same review-before-writing discipline as any other
  extraction.
- **When OCR-ing a batch of scanned pages or documents, check a few pages'
  output quality before committing to the full batch.** This is the
  test-3-5-before-bulk discipline applied specifically to OCR: a
  systematic misread (a font OCR handles badly, a skewed scan) shows up
  in the first few pages just as reliably as in the three-hundredth, and
  catching it early avoids reprocessing everything.

## Deferred: media ingest and archive crawling

Two broader capabilities exist in the legacy corpus this catalog draws
on but are **not yet automated here**. Both are real needs — a
video/audio/screenshot/repo ingestion pipeline, and a tool for crawling
a whole personal file archive (an old backup, a Dropbox export, a mail
takeout) for content worth keeping — but neither has a wakil-native
mechanism yet. For now, treat items that would fall under either as
ordinary instances of the source types above: normalize each one by hand
into a `text`/`article`/`transcript` shape plus a rich `--context`
string, and use `wakil ingest` directly, one item at a time.
