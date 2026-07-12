"""Prompt builders for grounded knowledge-base queries and enrichment.

System prompts for enrichment come from skills/*/SKILL.md via
`skill_loader.build_system_prompt` (prose judgment + Pydantic contract
schema); the builders here produce only the user-message content. The old
`INGEST_SYSTEM_PROMPT`/`parse_ingest_response` pair was retired in favor of
that split (docs/ingestion-refactor-spec.md).
"""

from dataclasses import dataclass

from wakil.schema.loader import EntitySchema

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


def build_extraction_prompt(
    source_type: str,
    origin: str,
    text: str,
    related_notes: list[tuple[str, str]],
    context: str | None = None,
    guides: dict[str, str] | None = None,
) -> str:
    """User content for the extraction call.

    related_notes: (path, title) pairs from searching the knowledge base.
    """
    parts = [f"Source type: {source_type}", f"Origin: {origin}", ""]
    if context:
        parts += ["User-provided context about this source:", context, ""]
    for name, content in (guides or {}).items():
        purpose = "page shape and metadata" if name == "SCHEMA.md" else "where notes belong"
        parts += [f"Workspace guidance from {name} ({purpose}):", content, ""]
    parts += [
        f"Existing related notes:\n{_render_related(related_notes)}",
        "",
        f"Source document:\n\n{text}",
    ]
    return "\n".join(parts)


def build_resolution_prompt(
    text: str,
    extraction_summary: str,
    proposed_note_markdown: str | None,
    related_notes: list[tuple[str, str]],
    entity_types: dict[str, EntitySchema],
    context: str | None = None,
    guides: dict[str, str] | None = None,
) -> str:
    """User content for the entity-resolution call.

    Unlike extraction, this call needs the candidate target pages and the
    catalog of known entity types as context — its question is "does this
    entity already have a page," not "what does the source say."
    """
    parts = [
        "Resolve every entity this source touched against the knowledge base.",
        "",
        f"Known entity types (use these exactly):\n{describe_entity_types(entity_types)}",
        "",
    ]
    if context:
        parts += ["User-provided context about this source:", context, ""]
    for name, content in (guides or {}).items():
        purpose = "page shape and metadata" if name == "SCHEMA.md" else "where notes belong"
        parts += [f"Workspace guidance from {name} ({purpose}):", content, ""]
    parts += ["Existing notes that may already cover these entities:"]
    parts += [_render_related(related_notes), ""]
    if extraction_summary:
        parts += [f"What the extraction step concluded:\n{extraction_summary}", ""]
    if proposed_note_markdown:
        parts += [f"The note the extraction step proposed:\n\n{proposed_note_markdown}", ""]
    parts += [f"Source document:\n\n{text}"]
    return "\n".join(parts)


def describe_entity_types(schemas: dict[str, EntitySchema]) -> str:
    """A compact, schema-derived catalog of entity types for the prompt."""
    lines = []
    for type_name, schema in sorted(schemas.items()):
        required = []
        for field_name, spec in schema.fields.items():
            if not spec.required:
                continue
            if spec.kind == "enum" and spec.values:
                required.append(f"{field_name} (one of: {', '.join(spec.values)})")
            else:
                required.append(field_name)
        directory = schema.directory or "(no canonical directory)"
        lines.append(
            f"- {type_name} — directory: {directory}; required fields: "
            f"{', '.join(required) if required else '(none)'}"
        )
    return "\n".join(lines)


def _render_related(related_notes: list[tuple[str, str]]) -> str:
    if not related_notes:
        return "(none found)"
    return "\n".join(f"- [[{path}]] {title}" for path, title in related_notes)


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
