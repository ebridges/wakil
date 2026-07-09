"""Prompt builders for grounded knowledge-base queries."""

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
