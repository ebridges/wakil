import json
from pathlib import Path

import pytest
from sqlalchemy import select

from wakil.app.ingest_service import (
    IngestError,
    apply_ingest,
    prepare_ingest,
    slugify,
    strip_srt,
)
from wakil.app.workspace_service import init_workspace, open_session
from wakil.config.settings import WorkspaceConfig
from wakil.storage.schema import IngestRun, Memory, Note, Relationship, Source

MODEL_JSON = {
    "title": "Claims Kickoff Meeting",
    "summary": "The team agreed to prototype FNOL routing using graph memory.",
    "key_points": ["Prototype approved", "Jane owns the routing design"],
    "memories": [
        {"type": "decision", "content": "Team will prototype FNOL routing.", "confidence": 0.9},
        {"type": "fact", "content": "Jane Doe owns the routing design.", "confidence": 0.8},
    ],
    "relationships": [{"subject": 0, "predicate": "related_to", "object": 1}],
    "proposed_note": {
        "path": "meetings/2026/2026-07-09-claims-kickoff.md",
        "markdown": (
            "---\ntype: meeting\ntitle: Claims Kickoff\n---\n\n"
            "# Claims Kickoff\n\nSee [[concepts/claims-routing.md]].\n"
        ),
    },
}


class FakeClient:
    model = "fake-model"

    def __init__(self, payload=None):
        self.payload = payload if payload is not None else MODEL_JSON
        self.prompts: list[str] = []

    def complete(self, system, prompt, max_tokens=8192):
        self.prompts.append(prompt)
        return json.dumps(self.payload) if isinstance(self.payload, dict) else self.payload


@pytest.fixture
def workspace(kb_path: Path) -> WorkspaceConfig:
    init_workspace(kb_path)
    return WorkspaceConfig.load(kb_path)


@pytest.fixture
def transcript(kb_path: Path) -> Path:
    path = kb_path / "raw-meeting.txt"
    path.write_text("Jane: let's prototype FNOL routing with graph memory.\nBob: agreed.\n")
    return path


def test_prepare_builds_full_proposal(workspace, transcript):
    client = FakeClient()
    proposal = prepare_ingest(workspace, "transcript", file=transcript, client=client)

    assert proposal.title == "Claims Kickoff Meeting"
    assert proposal.summary.startswith("The team agreed")
    assert len(proposal.memories) == 2
    assert proposal.memories[0].memory_type == "decision"
    assert len(proposal.relationships) == 1
    assert proposal.raw_file.path.startswith("sources/transcripts/")
    assert proposal.raw_file.content.startswith("---")
    assert proposal.proposed_note.path == "meetings/2026/2026-07-09-claims-kickoff.md"
    # Related notes from the fixture KB are offered to the model.
    assert "Source document" in client.prompts[0]


def test_prepare_without_client_still_works(workspace, transcript):
    proposal = prepare_ingest(workspace, "transcript", file=transcript)
    assert proposal.summary == ""
    assert proposal.memories == []
    assert proposal.raw_file.path.startswith("sources/transcripts/")


def test_apply_writes_files_and_records(workspace, transcript):
    proposal = prepare_ingest(workspace, "transcript", file=transcript, client=FakeClient())
    result = apply_ingest(workspace, proposal)

    root = workspace.root_path
    assert (root / proposal.raw_file.path).exists()
    assert (root / "meetings/2026/2026-07-09-claims-kickoff.md").exists()
    assert result.memories_created == 2
    assert result.relationships_created == 1

    with open_session(workspace) as session:
        source = session.scalar(select(Source))
        assert source.status == "ingested"
        assert source.raw_text_path == proposal.raw_file.path
        memories = list(session.scalars(select(Memory)))
        assert all(m.state == "candidate" for m in memories)
        assert all(m.source_id == source.id for m in memories)
        assert session.scalar(select(Relationship)) is not None
        run = session.scalar(select(IngestRun))
        assert run.status == "completed"
        # New files were re-indexed as notes.
        paths = set(session.scalars(select(Note.path)))
        assert proposal.raw_file.path in paths


def test_duplicate_ingest_is_detected(workspace, transcript):
    first = prepare_ingest(workspace, "transcript", file=transcript, client=FakeClient())
    apply_ingest(workspace, first)

    second = prepare_ingest(workspace, "transcript", file=transcript, client=FakeClient())
    assert second.duplicate_of is not None
    with pytest.raises(IngestError, match="already ingested"):
        apply_ingest(workspace, second)


def test_unsafe_note_path_falls_back_to_drafts(workspace, transcript):
    payload = dict(MODEL_JSON, proposed_note={"path": "../escape.md", "markdown": "# Escape\n"})
    proposal = prepare_ingest(workspace, "transcript", file=transcript, client=FakeClient(payload))
    assert proposal.proposed_note.path.startswith("drafts/")


def test_existing_note_path_falls_back_to_drafts(workspace, transcript):
    payload = dict(
        MODEL_JSON, proposed_note={"path": "concepts/claims-routing.md", "markdown": "# Clobber\n"}
    )
    proposal = prepare_ingest(workspace, "transcript", file=transcript, client=FakeClient(payload))
    assert proposal.proposed_note.path.startswith("drafts/")


def test_malformed_model_output_degrades_gracefully(workspace, transcript):
    proposal = prepare_ingest(
        workspace, "transcript", file=transcript, client=FakeClient("not json at all")
    )
    assert proposal.summary == "not json at all"
    assert proposal.memories == []
    assert proposal.proposed_note is None
    apply_ingest(workspace, proposal)  # still ingestable


def test_out_of_range_relationships_are_dropped(workspace, transcript):
    payload = dict(
        MODEL_JSON, relationships=[{"subject": 0, "predicate": "supports", "object": 99}]
    )
    proposal = prepare_ingest(workspace, "transcript", file=transcript, client=FakeClient(payload))
    result = apply_ingest(workspace, proposal)
    assert result.relationships_created == 0


def test_context_reaches_prompt_frontmatter_and_search(workspace, kb_path):
    transcript = kb_path / "standup.txt"
    transcript.write_text("Discussed the routing design with the team.\n")
    client = FakeClient()

    proposal = prepare_ingest(
        workspace,
        "transcript",
        file=transcript,
        client=client,
        context="Attendees: Jane Doe (Acme Corp), Bob. Weekly claims standup.",
    )

    # Context guides the model...
    assert "Jane Doe (Acme Corp)" in client.prompts[0]
    assert "User-provided context" in client.prompts[0]
    # ...lands in the raw capture's frontmatter...
    assert "context: 'Attendees: Jane Doe (Acme Corp)" in proposal.raw_file.content
    # ...and pulls entity notes into the related-note candidates.
    assert any(hit.ref == "people/jane-doe.md" for hit in proposal.related_notes)

    # It is persisted on the source record too.
    result = apply_ingest(workspace, proposal)
    with open_session(workspace) as session:
        source = session.get(Source, result.source_id)
    assert "Jane Doe" in source.metadata_json


def test_raw_capture_has_schema_style_metadata(workspace, transcript):
    proposal = prepare_ingest(workspace, "transcript", file=transcript)
    content = proposal.raw_file.content
    assert content.startswith("---\n")
    assert "type: source" in content
    assert "source_type: transcript" in content
    assert "status: raw" in content
    assert "retrieved:" in content


def test_workspace_guides_reach_prompt(workspace, transcript):
    # The fixture KB ships SCHEMA.md; add a RESOLVER.md as well.
    (workspace.root_path / "RESOLVER.md").write_text("# Resolver\n\nMeetings go in meetings/.\n")
    client = FakeClient()
    prepare_ingest(workspace, "transcript", file=transcript, client=client)

    prompt = client.prompts[0]
    assert "Workspace guidance from SCHEMA.md" in prompt
    assert "Workspace guidance from RESOLVER.md" in prompt
    assert "Meetings go in meetings/" in prompt


def test_clean_transcript_strips_timestamp_noise():
    from wakil.app.ingest_service import clean_transcript

    raw = (
        "[00:00:03] Jane:   let's start\n"
        "00:00:07 Bob: agreed,  see (00:01:12) above\n"
        "\n"
        "\n"
        "Jane: meet at 3:30 tomorrow\t\n"
    )
    assert clean_transcript(raw) == (
        "Jane: let's start\nBob: agreed, see above\n\nJane: meet at 3:30 tomorrow"
    )


def test_transcript_ingest_stores_cleaned_text(workspace, kb_path):
    noisy = kb_path / "noisy.txt"
    noisy.write_text("[00:00:01] Jane: hello\n00:00:05 Bob: hi\n")
    proposal = prepare_ingest(workspace, "transcript", file=noisy)
    assert "Jane: hello\nBob: hi" in proposal.raw_file.content
    assert "[00:00:01]" not in proposal.raw_file.content


def test_strip_srt():
    srt = (
        "1\n00:00:01,000 --> 00:00:03,000\nHello there.\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\nGeneral Kenobi.\n"
    )
    assert strip_srt(srt) == "Hello there.\nGeneral Kenobi."


def test_slugify():
    assert slugify("Claims Kickoff: Q3 / FNOL!") == "claims-kickoff-q3-fnol"
    assert slugify("   ") == "untitled"
