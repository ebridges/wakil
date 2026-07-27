import pytest
from pydantic import BaseModel, ValidationError

from wakil.llm.client import ModelTruncatedError
from wakil.llm.schemas import (
    CandidateMemoryModel,
    EntityCompileOutput,
    EntityResolution,
    EntityRevision,
    ExtractionOutput,
    ModelContractError,
    ProposedNoteModel,
    complete_with_contract,
)


class _Output(BaseModel):
    value: str


class _ScriptedClient:
    """Fake ModelClient that returns a scripted sequence of responses.

    Each entry is either a JSON string to return, or an exception instance
    to raise, in place of a real provider call.
    """

    model = "fake-model"

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[int] = []
        self.cacheable_prefixes: list[str | None] = []

    def complete(
        self,
        system: str,
        prompt: str,
        max_tokens: int = 8192,
        *,
        cacheable_prefix: str | None = None,
    ) -> str:
        self.calls.append(max_tokens)
        self.cacheable_prefixes.append(cacheable_prefix)
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def test_complete_with_contract_succeeds_first_try():
    client = _ScriptedClient(['{"value": "ok"}'])
    result = complete_with_contract(client, "sys", "prompt", _Output)
    assert result.value == "ok"
    assert client.calls == [8192]


def test_complete_with_contract_retries_invalid_json_without_growing_budget():
    client = _ScriptedClient(["not json", '{"value": "ok"}'])
    result = complete_with_contract(client, "sys", "prompt", _Output)
    assert result.value == "ok"
    assert client.calls == [8192, 8192]


def test_complete_with_contract_retries_truncation_with_doubled_budget():
    client = _ScriptedClient(
        [ModelTruncatedError(max_tokens=8192, partial='{"value": "cut off'), '{"value": "ok"}']
    )
    result = complete_with_contract(client, "sys", "prompt", _Output)
    assert result.value == "ok"
    assert client.calls == [8192, 16384]


def test_complete_with_contract_raises_after_second_truncation():
    client = _ScriptedClient(
        [
            ModelTruncatedError(max_tokens=8192, partial='{"value": "cut off'),
            ModelTruncatedError(max_tokens=16384, partial='{"value": "still cut off'),
        ]
    )
    with pytest.raises(ModelContractError) as exc_info:
        complete_with_contract(client, "sys", "prompt", _Output)
    assert "truncated" in str(exc_info.value)
    assert "max_tokens=16384" in str(exc_info.value)
    # Callers (e.g. entity-revision bisection, ADR 0015) branch on this to
    # decide whether splitting a batch and retrying could help at all.
    assert exc_info.value.truncated is True


def test_complete_with_contract_raises_after_second_invalid_response():
    client = _ScriptedClient(["not json", "still not json"])
    with pytest.raises(ModelContractError) as exc_info:
        complete_with_contract(client, "sys", "prompt", _Output)
    # A validation failure would recur identically on a smaller request for
    # an unrelated reason, so callers must not treat it like a truncation.
    assert exc_info.value.truncated is False


def test_complete_with_contract_passes_cacheable_prefix_unchanged_across_retry():
    client = _ScriptedClient(["not json", '{"value": "ok"}'])
    result = complete_with_contract(
        client, "sys", "prompt", _Output, cacheable_prefix="stable source text"
    )
    assert result.value == "ok"
    assert client.cacheable_prefixes == ["stable source text", "stable source text"]


def test_candidate_memory_model_accepts_opinion_type():
    memory = CandidateMemoryModel(type="opinion", content="X was worth it.", confidence=0.3)
    assert memory.type == "opinion"


def test_candidate_memory_model_rejects_out_of_range_confidence():
    with pytest.raises(ValidationError):
        CandidateMemoryModel(type="fact", content="X", confidence=1.5)
    with pytest.raises(ValidationError):
        CandidateMemoryModel(type="fact", content="X", confidence=-0.1)


def test_candidate_memory_model_accepts_boundary_confidence():
    assert CandidateMemoryModel(type="fact", content="X", confidence=0.0).confidence == 0.0
    assert CandidateMemoryModel(type="fact", content="X", confidence=1.0).confidence == 1.0


def test_candidate_memory_model_stance_defaults_to_none():
    assert CandidateMemoryModel(type="fact", content="X").stance is None


def test_entity_resolution_relevance_defaults_to_none():
    resolution = EntityResolution(name="X", entity_type="person", action="update")
    assert resolution.relevance is None


def test_entity_resolution_accepts_valid_relevance_values():
    for value in ("central", "notable", "minor", "peripheral"):
        resolution = EntityResolution(
            name="X", entity_type="person", action="update", relevance=value
        )
        assert resolution.relevance == value


def test_entity_resolution_rejects_invalid_relevance():
    with pytest.raises(ValidationError):
        EntityResolution(name="X", entity_type="person", action="update", relevance="urgent")


def test_entity_resolution_proposed_frontmatter_confidence_defaults_to_none():
    resolution = EntityResolution(name="X", entity_type="book", action="create")
    assert resolution.proposed_frontmatter_confidence is None


def test_entity_resolution_accepts_valid_proposed_frontmatter_confidence():
    resolution = EntityResolution(
        name="X", entity_type="book", action="create", proposed_frontmatter_confidence=0.2
    )
    assert resolution.proposed_frontmatter_confidence == 0.2


def test_entity_resolution_accepts_boundary_proposed_frontmatter_confidence():
    assert (
        EntityResolution(
            name="X", entity_type="book", action="create", proposed_frontmatter_confidence=0.0
        ).proposed_frontmatter_confidence
        == 0.0
    )
    assert (
        EntityResolution(
            name="X", entity_type="book", action="create", proposed_frontmatter_confidence=1.0
        ).proposed_frontmatter_confidence
        == 1.0
    )


def test_entity_resolution_rejects_out_of_range_proposed_frontmatter_confidence():
    with pytest.raises(ValidationError):
        EntityResolution(
            name="X", entity_type="book", action="create", proposed_frontmatter_confidence=1.5
        )
    with pytest.raises(ValidationError):
        EntityResolution(
            name="X", entity_type="book", action="create", proposed_frontmatter_confidence=-0.1
        )


def test_proposed_note_model_frontmatter_confidence_defaults_to_none():
    note = ProposedNoteModel(path="books/some-book.md", markdown="# Some Book\n")
    assert note.frontmatter_confidence is None


def test_proposed_note_model_accepts_valid_frontmatter_confidence():
    note = ProposedNoteModel(
        path="books/some-book.md", markdown="# Some Book\n", frontmatter_confidence=0.2
    )
    assert note.frontmatter_confidence == 0.2


def test_proposed_note_model_accepts_boundary_frontmatter_confidence():
    assert (
        ProposedNoteModel(
            path="books/some-book.md", markdown="# Some Book\n", frontmatter_confidence=0.0
        ).frontmatter_confidence
        == 0.0
    )
    assert (
        ProposedNoteModel(
            path="books/some-book.md", markdown="# Some Book\n", frontmatter_confidence=1.0
        ).frontmatter_confidence
        == 1.0
    )


def test_proposed_note_model_rejects_out_of_range_frontmatter_confidence():
    with pytest.raises(ValidationError):
        ProposedNoteModel(
            path="books/some-book.md", markdown="# Some Book\n", frontmatter_confidence=1.5
        )
    with pytest.raises(ValidationError):
        ProposedNoteModel(
            path="books/some-book.md", markdown="# Some Book\n", frontmatter_confidence=-0.1
        )


def test_extraction_output_carries_proposed_note_frontmatter_confidence():
    output = ExtractionOutput(
        proposed_note=ProposedNoteModel(
            path="books/some-book.md",
            markdown="# Some Book\n",
            frontmatter_confidence=0.3,
        )
    )
    assert output.proposed_note.frontmatter_confidence == 0.3


def test_candidate_memory_model_accepts_valid_stance_values():
    assert CandidateMemoryModel(type="fact", content="X", stance="casual").stance == "casual"
    assert CandidateMemoryModel(type="fact", content="X", stance="formal").stance == "formal"


def test_candidate_memory_model_rejects_invalid_stance():
    with pytest.raises(ValidationError):
        CandidateMemoryModel(type="fact", content="X", stance="urgent")


def test_entity_compile_output_accepts_compiled_truth():
    output = EntityCompileOutput(compiled_truth="The re-synthesized top section.")
    assert output.compiled_truth == "The re-synthesized top section."


def test_entity_compile_output_rejects_missing_compiled_truth():
    with pytest.raises(ValidationError):
        EntityCompileOutput()


def test_entity_revision_confidence_defaults_to_none():
    revision = EntityRevision(target_note_path="people/x.md", has_update=True)
    assert revision.confidence is None


def test_entity_revision_accepts_valid_confidence():
    revision = EntityRevision(target_note_path="people/x.md", has_update=True, confidence=0.2)
    assert revision.confidence == 0.2


def test_entity_revision_accepts_boundary_confidence():
    assert (
        EntityRevision(target_note_path="p.md", has_update=True, confidence=0.0).confidence == 0.0
    )
    assert (
        EntityRevision(target_note_path="p.md", has_update=True, confidence=1.0).confidence == 1.0
    )


def test_entity_revision_rejects_out_of_range_confidence():
    with pytest.raises(ValidationError):
        EntityRevision(target_note_path="p.md", has_update=True, confidence=1.5)
    with pytest.raises(ValidationError):
        EntityRevision(target_note_path="p.md", has_update=True, confidence=-0.1)
