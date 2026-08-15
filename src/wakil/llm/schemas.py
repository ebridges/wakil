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

from wakil.llm.client import DEFAULT_MAX_TOKENS, ModelClient, ModelTruncatedError


class ModelContractError(RuntimeError):
    """The model failed to produce schema-valid output twice in a row."""

    def __init__(self, contract: str, detail: str, *, truncated: bool):
        self.contract = contract
        self.detail = detail
        # Distinguishes "kept hitting max_tokens" from "kept producing
        # malformed JSON" — only the former is something a caller can
        # meaningfully react to (e.g. by splitting a batch and retrying;
        # see ADR 0015). A validation failure will recur identically on a
        # smaller request for an unrelated reason, so it isn't.
        self.truncated = truncated
        super().__init__(f"{contract}: model output failed validation twice: {detail}")


class CandidateMemoryModel(BaseModel):
    type: str = Field(
        default="fact",
        description="fact | opinion | summary | relationship | question | hypothesis | decision "
        "| theme | event (event = something dated that happened; opinion = a subjective "
        "value judgment, stance, or interpretation attributed to a speaker, distinct from "
        "fact — an observed/stated actuality)",
    )
    content: str = Field(description="One self-contained claim, observation, or question.")
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    stance: Literal["formal", "casual"] | None = Field(
        default=None,
        description="casual = asserted in a low-commitment register (an off-the-cuff "
        "1:1 remark the speaker would not stand behind formally); formal or omitted "
        "otherwise. Orthogonal to `type` — a casual opinion and a casual fact are both "
        "valid. Distinct from confidence: this is about the register a claim was "
        "uttered in, not how certain it is.",
    )
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
    frontmatter_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="How well-supported markdown's frontmatter field values are by "
        "the source, not whether creating the note at all was warranted (this is "
        "extraction's own proposed_note, not an entity-resolution create, so the "
        "concept mirrors `EntityResolution.proposed_frontmatter_confidence` here). "
        "A field asserted from thin or ambiguous evidence (e.g. a book's `status: "
        "finished` inferred from a single early highlight with no explicit "
        "completion signal) gets low confidence even though the note clearly "
        "merits creation; a clearly-stated field gets high confidence. Omit or "
        "leave null when the source's support is unambiguous enough that a "
        "confidence figure wouldn't add information.",
    )


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


class CaptureMetadata(BaseModel):
    """Capture-time model call (docs/adr/0010): a short title and a dense
    abstract for a freshly captured source, generated once so the raw
    file's frontmatter carries durable, content-derived context beyond
    the filename."""

    title: str = Field(description="yyyy-mm-dd prefixed, descriptive, under 60 characters.")
    abstract: str = Field(description="~300 characters, dense enough for retrieval/search.")


class EntityResolution(BaseModel):
    """Second model call, one decision per mentioned entity."""

    name: str = Field(description="The entity's canonical display name.")
    entity_type: str = Field(description="One of the entity types listed in the prompt.")
    action: Literal["create", "update", "skip"]
    target_note_path: str | None = Field(
        default=None, description="Existing note path, for action=update."
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Identity-match certainty only — how sure this mention resolves to "
        "this specific existing page, not how much the source discusses it. An "
        "unambiguous, distinctly-named entity resolves with high confidence even if "
        "it's mentioned only in passing; a common or ambiguous name gets lower "
        "confidence even if it's central to the source. See `relevance` for the "
        "separate question of how much the source actually concerns this entity.",
    )
    # Deliberately a pointer rather than a second definition of the four levels.
    # This schema only ever reaches a model through
    # `build_system_prompt(skill, EntityResolutionOutput)` (`_run_entity_resolution`),
    # which puts `entity-resolve/SKILL.md`'s body in the same system message — so the
    # levels are always present, and stating them twice lets the two copies drift.
    # They did: this description sat unchanged from ADR 0015 while the skill replaced
    # its focus test ("mentioned but not a focus") with a substance test, leaving the
    # model two different tests for `minor` in one message (PR #239 review). Nothing
    # can catch a repeat — `tests/evals/runner.py` builds its system prompt from
    # `skill.body` alone, with no schema block at all.
    relevance: Literal["central", "notable", "minor", "peripheral"] | None = Field(
        default=None,
        description="How much this source substantively concerns this entity — a "
        "separate question from `confidence`, which is identity-match certainty. The "
        "four levels are defined in the skill body's \"Source relevance gate\"; apply "
        "those definitions.",
    )
    proposed_frontmatter: dict | None = Field(
        default=None,
        description="For action=create: frontmatter satisfying the type's required "
        "fields. Ignored for action=update.",
    )
    proposed_frontmatter_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="For action=create only: how well-supported proposed_frontmatter's "
        "field values are by the source, not whether creating the page at all was "
        "warranted — that's `action`/`relevance`. A field asserted from thin or "
        "ambiguous evidence (e.g. a book's `status: finished` inferred from a single "
        "early highlight with no explicit completion signal) gets low confidence even "
        "though the entity clearly merits a page; a clearly-stated field gets high "
        "confidence. Omit or leave null when the source's support is unambiguous "
        "enough that a confidence figure wouldn't add information.",
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
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="For has_update=True only: how well-supported this revision's "
        "content is by the source, not whether an update was warranted at all — "
        "that's `has_update`. A compiled_truth/frontmatter_updates value inferred "
        "from thin or ambiguous evidence (e.g. a single early highlight implying a "
        "book's `status` without an explicit completion signal) gets low confidence "
        "even though has_update is true; a clearly-stated fact gets high confidence. "
        "Omit or leave null when the source's support is unambiguous enough that a "
        "confidence figure wouldn't add information.",
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


class EntityCompileOutput(BaseModel):
    """The entity-compile pilot's one-step DAG (`wakil entities compile
    SLUG`, docs/adr/0016): re-synthesize a single entity's Compiled Truth
    from its own existing Timeline, with no other source involved.

    A minimal contract rather than reusing `EntityRevision`, deliberately:
    `has_update` (a gate against a *new* mention that might not warrant a
    change) and `timeline_entry` (a *new* dated entry to prepend) don't
    apply here — compile always produces a result and never touches
    Timeline, so a schema that can't even represent the fields this call
    must never set is safer than one that can but is expected not to.
    """

    compiled_truth: str = Field(
        description="The full re-synthesized Compiled Truth section: the union "
        "of every fact already present anywhere in the Timeline text given — "
        "additive-only synthesis, never a lossy summary. Omitting a fact that "
        "is still true and present in the Timeline is a defect, not an "
        "acceptable simplification. If genuinely unsure whether two Timeline "
        "entries describe the same fact or two different facts, include both "
        "rather than merging or dropping either."
    )


def validate_model_response[T: BaseModel](raw: str, schema: type[T]) -> T:
    """Strip code fences and validate; raises pydantic.ValidationError."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    return schema.model_validate_json(cleaned)


def complete_with_contract[T: BaseModel](
    client: ModelClient,
    system: str,
    prompt: str,
    schema: type[T],
    *,
    cacheable_prefix: str | None = None,
) -> T:
    """One model call validated against `schema`, with a single retry.

    On the first validation failure the error is appended to the prompt and
    the call repeated. On a truncated response (the model hit max_tokens
    before finishing its JSON) the prompt is unchanged but the budget is
    doubled — re-sending the same prompt at the same length would just
    truncate again. Either way, a second failure raises ModelContractError
    with the underlying detail so the caller can surface it visibly instead
    of a bare JSON-parse error.

    `cacheable_prefix` is passed straight through to the client on every
    attempt, unchanged — only `prompt` grows (the validation-error retry
    text is appended to it), so the prefix stays byte-identical across
    retries and is eligible for a prompt-cache hit.
    """
    max_tokens = DEFAULT_MAX_TOKENS
    for attempt in (1, 2):
        try:
            raw = client.complete(
                system, prompt, max_tokens=max_tokens, cacheable_prefix=cacheable_prefix
            )
            return validate_model_response(raw, schema)
        except ModelTruncatedError as exc:
            if attempt == 2:
                raise ModelContractError(schema.__name__, str(exc), truncated=True) from exc
            max_tokens *= 2
        except ValidationError as exc:
            if attempt == 2:
                raise ModelContractError(schema.__name__, str(exc), truncated=False) from exc
            prompt = (
                f"{prompt}\n\n"
                f"Your previous response was not valid:\n{exc}\n\n"
                "Respond again with ONLY a single JSON object conforming to the schema "
                "in the system prompt — no code fences, no prose."
            )
    raise AssertionError("unreachable")  # pragma: no cover
