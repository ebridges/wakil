"""Pydantic contracts for model output, and the validate-with-one-retry loop.

These models are the single source of truth for the JSON shapes the model
must produce: the same `model_json_schema()` shown in the system prompt is
what `validate_model_response` validates against, so prompt and validator
can never drift apart (docs/ingestion-refactor-spec.md).

A response that fails validation is retried once with the error appended to
the prompt; a second failure raises `ModelContractError` — a visible
failure, never a silent coercion to an empty shape.
"""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from wakil.llm.client import ModelClient


class ModelContractError(RuntimeError):
    """The model failed to produce schema-valid output twice in a row."""

    def __init__(self, contract: str, detail: str):
        self.contract = contract
        self.detail = detail
        super().__init__(f"{contract}: model output failed validation twice: {detail}")


class CandidateMemoryModel(BaseModel):
    type: str = Field(
        default="fact",
        description="fact | summary | relationship | question | hypothesis | decision "
        "| theme | event (event = something dated that happened)",
    )
    content: str = Field(description="One self-contained claim, observation, or question.")
    confidence: float | None = None
    event_date: dt.date | None = Field(
        default=None,
        description="ISO date of the event itself, only for type=event.",
    )


class CandidateRelationshipModel(BaseModel):
    subject: int = Field(description="0-based index into the memories array.")
    predicate: str = Field(
        description="supports | contradicts | elaborates | mentions | related_to | raises_question"
    )
    object: int = Field(description="0-based index into the memories array.")


class ProposedNoteModel(BaseModel):
    path: str = Field(description="Workspace-relative Markdown path for the new note.")
    markdown: str = Field(description="Full note content including YAML frontmatter.")


class ExtractionOutput(BaseModel):
    """First model call: what does this source say?"""

    title: str | None = Field(default=None, description="Short descriptive title.")
    summary: str = Field(default="", description="2-5 sentence summary.")
    key_points: list[str] = Field(default_factory=list)
    memories: list[CandidateMemoryModel] = Field(default_factory=list)
    relationships: list[CandidateRelationshipModel] = Field(default_factory=list)
    proposed_note: ProposedNoteModel | None = Field(
        default=None,
        description="A durable KB note for this source, or null if it does not merit one.",
    )


class EntityResolution(BaseModel):
    """Second model call, one decision per mentioned entity."""

    name: str = Field(description="The entity's canonical display name.")
    entity_type: str = Field(description="One of the entity types listed in the prompt.")
    action: Literal["create", "update", "skip"]
    target_note_path: str | None = Field(
        default=None, description="Existing note path, for action=update."
    )
    confidence: float | None = None
    proposed_frontmatter: dict | None = Field(
        default=None,
        description="For action=create: frontmatter satisfying the type's required "
        "fields. Ignored for action=update.",
    )


class EntityResolutionOutput(BaseModel):
    entities: list[EntityResolution] = Field(default_factory=list)


class EntityRevision(BaseModel):
    """Third model call, one decision per action=update entity: does this
    mention actually warrant touching the page, and if so, how."""

    target_note_path: str = Field(description="Must match one of the update targets given.")
    has_update: bool = Field(
        description="False when the mention is too light to change the page — "
        "no compiled_truth/timeline_entry/frontmatter_updates needed."
    )
    compiled_truth: str | None = Field(
        default=None,
        description="For has_update=True: the full re-synthesized top section — "
        "the union of what was already there plus what's new, never just the new "
        "source's content. Do NOT include the '# Title' heading line itself (kept "
        "as-is) or the 'Timeline / Log' heading; only the content between them.",
    )
    timeline_entry: str | None = Field(
        default=None,
        description="For has_update=True: one new dated entry to prepend to the "
        "Timeline, e.g. '### 2026-07-16 — what happened\\n- detail'. Existing "
        "entries are never touched.",
    )
    frontmatter_updates: dict | None = Field(
        default=None, description="Only the frontmatter fields that should change."
    )


class EntityRevisionOutput(BaseModel):
    revisions: list[EntityRevision] = Field(default_factory=list)


def validate_model_response[T: BaseModel](raw: str, schema: type[T]) -> T:
    """Strip code fences and validate; raises pydantic.ValidationError."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    return schema.model_validate_json(cleaned)


def complete_with_contract[T: BaseModel](
    client: ModelClient, system: str, prompt: str, schema: type[T]
) -> T:
    """One model call validated against `schema`, with a single retry.

    On the first validation failure the error is appended to the prompt and
    the call repeated; a second failure raises ModelContractError so the
    caller can surface it visibly.
    """
    raw = client.complete(system, prompt)
    try:
        return validate_model_response(raw, schema)
    except ValidationError as first:
        retry_prompt = (
            f"{prompt}\n\n"
            f"Your previous response was not valid:\n{first}\n\n"
            "Respond again with ONLY a single JSON object conforming to the schema "
            "in the system prompt — no code fences, no prose."
        )
        raw = client.complete(system, retry_prompt)
        try:
            return validate_model_response(raw, schema)
        except ValidationError as second:
            raise ModelContractError(schema.__name__, str(second)) from second
