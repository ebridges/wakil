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


CAPTURE_METADATA_SYSTEM_PROMPT = """\
You are wakil, a careful assistant for a personal Markdown knowledge base.

A source has just been captured. Write a short title and a brief abstract \
for it, grounded only in the source document given. Respond with a single \
JSON object matching the schema given in the request — no code fences, no \
prose outside the JSON.
"""


@dataclass
class ContextBlock:
    """One numbered piece of grounding context shown to the model."""

    ref: str  # note path, "memory:<id>", or "source:<id>"
    kind: str  # note | memory | source
    title: str
    text: str


def build_capture_metadata_prompt(
    source_type: str,
    origin: str,
    text: str,
    today: str,
    context: str | None = None,
) -> str:
    """User content for the capture-time title/abstract call (docs/adr/0010).

    A single, cheap model call made once at capture time — everything else
    about the raw capture stays deterministic; only these two frontmatter
    fields come from the model.
    """
    parts = [
        f"Source type: {source_type}",
        f"Origin: {origin}",
        "",
        "Write a title and an abstract for this captured source.",
        "",
        "Title rules:",
        f"- Prefixed with the date of the ingest: {today}",
        "- Descriptive enough to identify the page from a search result",
        "- Short enough to scan in a list (under 60 characters)",
        '- NOT a sentence ("Meeting with Pedro" not "Meeting with Pedro about '
        'the new deal structure")',
        '- NOT generic ("Pedro Franceschi" not "Person Page")',
        "",
        "Abstract: roughly 300 characters, dense enough to be useful for "
        "retrieval and search — not a full summary.",
        "",
    ]
    if context:
        parts += ["User-provided context about this source:", context, ""]
    parts += [f"Source document:\n\n{text}"]
    return "\n".join(parts)


def build_extraction_prompt(
    source_type: str,
    origin: str,
    text: str,
    related_notes: list[tuple[str, str]],
    entity_types: dict[str, EntitySchema],
    page_shapes: dict[str, str],
    context: str | None = None,
    guides: dict[str, str] | None = None,
) -> str:
    """User content for the extraction call.

    related_notes: (path, title) pairs from searching the knowledge base.
    entity_types: wakil's own resolved schema catalog (kb-local/user/built-in)
    — the structural source of truth for a proposed note's frontmatter shape,
    independent of whatever survives `guides`' truncation.
    page_shapes: shape name -> resolved template body, one entry per distinct
    `page_shape` value used by `entity_types` (pre-resolved by the caller via
    `wakil.schema.loader.resolve_page_shape_template` — this module stays
    I/O-free). Each type below names its shape; the matching body tells the
    model what that shape actually looks like.
    """
    parts = [f"Source type: {source_type}", f"Origin: {origin}", ""]
    if context:
        parts += ["User-provided context about this source:", context, ""]
    parts += [
        "If you propose a note, its frontmatter must be valid for its type "
        "below: every required field filled, and every optional field filled "
        "too whenever the source actually supports it — don't leave a field "
        "blank by default just because it isn't required. It must also "
        "include a `type: <name>` line of its own matching that type "
        "exactly (e.g. `type: meeting`) — this is required for every type "
        "even though `type` is never listed among that type's fields below, "
        "since it names the type rather than being one of its fields. Its "
        "body must follow that type's page_shape — match the shape name "
        "against the templates that follow. Also set "
        "proposed_note.frontmatter_confidence: how well-supported the "
        "frontmatter values you wrote into markdown are by the source (e.g. "
        "a book's status field inferred from a single thin or ambiguous "
        "highlight gets low confidence), not whether proposing the note at "
        "all was warranted.",
        "When tagging a memory's `type`, distinguish observed fact from subjective "
        "judgment: a claim asserting what *is* (a number, an event, a stated position) "
        "is `fact`; a claim asserting what is good/bad/worth-it, or a causal 'because' "
        "clause the source doesn't itself establish, is interpretation and belongs as "
        "`opinion`, not `fact`.",
        describe_entity_types_full(entity_types),
        "",
        describe_page_shapes(page_shapes),
        "",
    ]
    for name, content in (guides or {}).items():
        parts += [f"Workspace guidance from {name} (where notes belong):", content, ""]
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
        "For action=create, also set proposed_frontmatter_confidence: how "
        "well-supported proposed_frontmatter's field values are by the source "
        "(e.g. a status field inferred from a single thin or ambiguous mention "
        "gets low confidence), not whether creating the page at all was warranted.",
        "",
        f"Known entity types (use these exactly):\n{describe_entity_types(entity_types)}",
        "",
    ]
    if context:
        parts += ["User-provided context about this source:", context, ""]
    for name, content in (guides or {}).items():
        parts += [f"Workspace guidance from {name} (where notes belong):", content, ""]
    parts += ["Existing notes that may already cover these entities:"]
    parts += [_render_related(related_notes), ""]
    if extraction_summary:
        parts += [f"What the extraction step concluded:\n{extraction_summary}", ""]
    if proposed_note_markdown:
        parts += [f"The note the extraction step proposed:\n\n{proposed_note_markdown}", ""]
    parts += [f"Source document:\n\n{text}"]
    return "\n".join(parts)


def build_revision_prompt(
    text: str,
    extraction_summary: str,
    targets: list[tuple[str, str]],
    context: str | None = None,
) -> tuple[str, str]:
    """User content for the entity-update call (DAG node 3).

    targets: (target_note_path, current_full_content) for every entity
    entity-resolution decided should be updated. The system prompt is
    `note-revision/SKILL.md` itself — its "read the existing note in full
    before writing anything" rule is why the full content is inlined here
    rather than a summary or a diff.

    Scoped to compiled-truth-timeline-shaped entities only (the caller
    filters); note-revision's own discipline (State vs. Timeline) doesn't
    define what an update means for a single-occurrence type.

    Returns (cacheable_prefix, variable_suffix) instead of one string: the
    source document and its context are identical across every retry of
    this call, and across any future per-batch sub-calls that share the
    same source, so they're kept separate and placed first so the caller
    can mark them as a cached prefix. The target notes go last, closest to
    the instructions asking the model to act on them — both because that's
    the part that differs per batch, and because it puts the concrete task
    right before generation starts.
    """
    prefix_parts = []
    if context:
        prefix_parts += ["User-provided context about this source:", context, ""]
    if extraction_summary:
        prefix_parts += [f"What this source is about:\n{extraction_summary}", ""]
    prefix_parts += [f"Source document:\n\n{text}"]
    cacheable_prefix = "\n".join(prefix_parts)

    suffix_parts = [
        "For each existing note below, decide whether this source's mention "
        "of that entity actually warrants updating the page (has_update) — "
        "a passing reference that adds no new fact gets has_update=false, "
        "no compiled_truth/timeline_entry/frontmatter_updates. When it does "
        "warrant an update: compiled_truth re-synthesizes the union of what "
        "was already there plus what's new (never just the new source), and "
        "timeline_entry is one new dated entry to prepend — existing "
        "entries, including any auto-generated back-link lines, are never "
        "restated or reordered. Also set confidence: how well-supported the "
        "update's content is by the source (e.g. a frontmatter field inferred "
        "from a single thin or ambiguous mention gets low confidence), not "
        "whether an update was warranted at all.",
        "",
    ]
    for path, content in targets:
        suffix_parts += [f"### Existing note to consider: {path}\n\n{content}", ""]
    variable_suffix = "\n".join(suffix_parts).rstrip("\n")

    return cacheable_prefix, variable_suffix


def build_compile_prompt(
    entity_name: str, top_section: str, timeline_section: str
) -> tuple[str, str]:
    """User content for the entity-compile call (`wakil entities compile
    SLUG`, docs/adr/0016).

    Unlike build_revision_prompt, there is no separate source document to
    inline — the entity's own Timeline *is* the source, and it's what's
    byte-identical across any retry of this call. Returns (cacheable_prefix,
    variable_suffix): the Timeline goes in cacheable_prefix so it's eligible
    for a prompt-cache hit; the note's current top section and the task
    instructions go in variable_suffix, instructions last, so the concrete
    ask sits right before generation starts — the same "long content first,
    task last" convention build_revision_prompt documents in its own
    docstring.
    """
    cacheable_prefix = f"{entity_name}'s full Timeline / Log:\n\n{timeline_section}"

    suffix_parts = [
        f"{entity_name}'s current Compiled Truth (top section):\n\n"
        f"{top_section or '(empty — no Compiled Truth has been written yet)'}",
        "",
        "Re-synthesize Compiled Truth for this entity as the union of every "
        "fact already present anywhere in the Timeline above — additive-only "
        "synthesis, never a lossy summary. Every fact already present in the "
        "Timeline must be present, in some form, in your output; omitting one "
        "is a defect, not an acceptable simplification. If genuinely unsure "
        "whether two Timeline entries describe the same fact or two "
        "different facts, include both rather than merging or dropping "
        "either.",
    ]
    variable_suffix = "\n".join(suffix_parts)

    return cacheable_prefix, variable_suffix


def build_full_resynthesis_prompt(entity_name: str, timeline_section: str) -> tuple[str, str]:
    """User content for the full-resynthesis compile call (`wakil entities
    compile SLUG --full`, docs/adr/0017 Stage 2).

    Deliberately narrower than `build_compile_prompt`: there is no
    `top_section`/current-Compiled-Truth parameter at all. This is not an
    oversight carried over from `build_compile_prompt` — it's ADR
    0017-mandated. Full resynthesis, unlike additive mode, is allowed to
    drop content; showing the model "here is the current summary" alongside
    the full Timeline risks anchoring it on whatever a *previous* run
    already decided to compress, rather than re-deriving fresh from source
    every time — and if that bias is real, it compounds across repeated
    invocations with no floor. Keeping this prompt strictly Timeline-derived
    prevents that by construction: every full resynthesis is a genuinely
    fresh re-derivation from the one thing that's actually immutable, not an
    edit of the last one.

    Returns (cacheable_prefix, variable_suffix): the Timeline goes in
    cacheable_prefix so it's eligible for a prompt-cache hit; the task
    instructions go in variable_suffix, last, so the concrete ask sits right
    before generation starts — the same "long content first, task last"
    convention `build_compile_prompt` documents in its own docstring.
    """
    cacheable_prefix = f"{entity_name}'s full Timeline / Log:\n\n{timeline_section}"

    suffix_parts = [
        "Fully re-synthesize Compiled Truth for this entity from the "
        "Timeline above only — this is full resynthesis, not the ordinary "
        "additive merge, and no current Compiled Truth is given to you, on "
        "purpose (see entity-compile/SKILL.md's \"Full resynthesis mode\" "
        "section for why). Apply that section's two-part salience rule: "
        "collapse a fact restated or updated across multiple entries toward "
        "its current value, dropping an earlier value entirely where a "
        "later entry clearly and unambiguously supersedes it; keep every "
        "durable once-stated fact (identifying details, decisions, "
        "commitments, ongoing status or relationship facts) intact "
        "regardless of size pressure; compress or drop an ephemeral "
        "once-stated fact (a scheduling detail, a meeting-day operational "
        "note) only once whatever durable consequence it led to is already "
        "captured elsewhere in your output. When genuinely uncertain "
        "whether a once-stated fact is durable or ephemeral, default to "
        "durable and keep it — wrongly keeping something only costs a "
        "little verbosity, wrongly dropping it silently deletes a true "
        "fact.",
    ]
    variable_suffix = "\n".join(suffix_parts)

    return cacheable_prefix, variable_suffix


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


def describe_entity_types_full(schemas: dict[str, EntitySchema]) -> str:
    """Every entity type's complete field shape (required + optional).

    Unlike `describe_entity_types` (required fields only, for matching an
    entity against a compact catalog), extraction needs the full menu of
    optional fields too — this is what tells the model a `meeting` note can
    carry `decisions`/`action-items`/`transcript`, not just `title`/`date`.
    """
    lines = []
    for type_name, schema in sorted(schemas.items()):
        directory = schema.directory or "(no canonical directory)"
        lines.append(
            f"- {type_name} (directory: {directory}, category: {schema.category}, "
            f"page_shape: {schema.page_shape})"
        )
        for field_name, spec in schema.fields.items():
            requirement = "required" if spec.required else "optional"
            kind = spec.kind
            if kind == "enum" and spec.values:
                kind = f"enum: {', '.join(spec.values)}"
            lines.append(f"    - {field_name} ({requirement}, {kind})")
    return "\n".join(lines)


def describe_page_shapes(page_shapes: dict[str, str]) -> str:
    """Render each distinct page-shape template once, labeled by name."""
    parts = []
    for shape, body in sorted(page_shapes.items()):
        parts.append(f"### Page shape '{shape}'\n\n{body}")
    return "\n\n".join(parts)


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
