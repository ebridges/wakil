# common_words.zip provenance

- **Source:** [hermitdave/FrequencyWords](https://github.com/hermitdave/FrequencyWords) — English word-frequency
  counts derived from OpenSubtitles.
- **License:** MIT (per the FrequencyWords repository).
- **Content:** the ~5,000 most frequent English words, one per line, ranked by frequency (most common first).
- **Vendored:** 2026-07-21, for `_candidate_entity_notes` in `src/wakil/app/ingest_service.py` — filters
  single-token candidates that are ordinary vocabulary (function words, backchannel markers, common
  nouns/adjectives swept up during small talk) rather than genuine proper nouns.
- **Format:** zipped (`common_words.zip`, member `common_words.txt`) to keep the vendored file small in the
  repo; decompressed once at import time via `zipfile` (already a stdlib dependency used elsewhere in
  `ingest_service.py` for `.whisper` transcript parsing).

To refresh: pull an updated frequency list from the source repo, take the top 5,000 entries (one word per
line, no counts), and re-zip as `common_words.zip` with member name `common_words.txt`.
