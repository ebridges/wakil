"""Prompt builders for grounded knowledge-base queries and ingests."""

import json
from dataclasses import dataclass

QUERY_SYSTEM_PROMPT = """\
You are wakil, a careful assistant for a personal Markdown knowledge base.

Answer the user's question using ONLY the numbered context blocks provided.
Rules:
- Ground every claim in the context; cite blocks inline as [1], [2], etc.
- If the context does not support an answer, say so plainly instead of guessing.
- Point out useful connections between contexts when they exist.
- Write readable Markdown suitable for a terminal.
- End with a short "Follow-up questions:" list (2-3 items) grounded in the context.
"""


@dataclass
class ContextBlock:
    """One numbered piece of grounding context shown to the model."""

    ref: str  # note path, "memory:<id>", or "source:<id>"
    kind: str  # note | memory | source
    title: str
    text: str


INGEST_SYSTEM_PROMPT = """\
You are wakil, a careful assistant for a personal Markdown knowledge base.

You are given a newly ingested source document plus a list of existing notes
that may be related. Produce structured knowledge from it.

Respond with a single JSON object and nothing else (no code fences):

{
  "title": "short descriptive title for the source",
  "summary": "2-5 sentence summary",
  "key_points": ["...", "..."],
  "memories": [
    {"type": "fact|summary|relationship|question|hypothesis|decision|theme",
     "content": "one self-contained claim, observation, or question",
     "confidence": 0.0-1.0}
  ],
  "relationships": [
    {"subject": <memory index>, "predicate":
     "supports|contradicts|elaborates|mentions|related_to|raises_question",
     "object": <memory index>}
  ],
  "proposed_note": {
    "path": "relative/markdown/path.md",
    "markdown": "full note content with YAML frontmatter"
  }
}

Rules:
- Memories must be grounded in the source; do not invent facts.
- 3-10 memories; relationships only where clearly justified.
- `subject`/`object` are 0-based indices into the memories array.
- Link related existing notes with [[wikilinks]] in the proposed note when
  they are genuinely relevant.
- Set "proposed_note" to null if the source does not merit a durable note.
- Keep the proposed note faithful to the source; mark uncertainty explicitly.
"""


def build_ingest_prompt(
    source_type: str, origin: str, text: str, related_notes: list[tuple[str, str]]
) -> str:
    """related_notes: (path, title) pairs from searching the knowledge base."""
    related = (
        "\n".join(f"- [[{path}]] {title}" for path, title in related_notes)
        if related_notes
        else "(none found)"
    )
    return (
        f"Source type: {source_type}\n"
        f"Origin: {origin}\n\n"
        f"Existing related notes:\n{related}\n\n"
        f"Source document:\n\n{text}"
    )


def parse_ingest_response(raw: str) -> dict:
    """Parse the model's ingest JSON defensively.

    Falls back to treating the whole response as a summary so a malformed
    reply still yields a usable (if minimal) result.
    """
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    try:
        data = json.loads(cleaned)
        if not isinstance(data, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        return {
            "summary": raw.strip(),
            "key_points": [],
            "memories": [],
            "relationships": [],
            "proposed_note": None,
        }

    data.setdefault("summary", "")
    data.setdefault("key_points", [])
    data.setdefault("memories", [])
    data.setdefault("relationships", [])
    data["memories"] = [m for m in data["memories"] if isinstance(m, dict) and m.get("content")]
    data["relationships"] = [
        r
        for r in data["relationships"]
        if isinstance(r, dict) and {"subject", "predicate", "object"} <= r.keys()
    ]
    note = data.get("proposed_note")
    if not (isinstance(note, dict) and note.get("path") and note.get("markdown")):
        data["proposed_note"] = None
    return data


def build_query_prompt(question: str, contexts: list[ContextBlock]) -> str:
    if not contexts:
        return (
            "No matching context was found in the knowledge base.\n\n"
            f"Question: {question}\n\n"
            "State that the knowledge base has no material on this question."
        )
    parts = []
    for i, block in enumerate(contexts, start=1):
        parts.append(f"[{i}] ({block.kind}: {block.ref}) {block.title}\n{block.text}")
    joined = "\n\n---\n\n".join(parts)
    return f"Context blocks:\n\n{joined}\n\n---\n\nQuestion: {question}"
