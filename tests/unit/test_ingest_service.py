import json
from pathlib import Path

import pytest
from sqlalchemy import select

from wakil.app.ingest_service import (
    IngestError,
    apply_capture,
    apply_enrichment,
    clean_transcript,
    infer_meeting_date,
    prepare_capture,
    prepare_enrichment,
    slugify,
    strip_srt,
    transcript_frontmatter_template,
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
            "# Claims Kickoff\n\nAttended by [[people/jane-doe.md]]. "
            "See [[concepts/claims-routing.md]].\n"
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
    path = kb_path / "2026-07-09-raw-meeting.txt"
    path.write_text("Jane: let's prototype FNOL routing with graph memory.\nBob: agreed.\n")
    return path


def _capture(workspace, transcript, context=None) -> int:
    proposal = prepare_capture(workspace, "transcript", file=transcript, context=context)
    return apply_capture(workspace, proposal).source_id


# --------------------------------------------------------------------------
# Capture


def test_capture_is_model_free_and_minimal(workspace, transcript):
    proposal = prepare_capture(workspace, "transcript", file=transcript)

    assert proposal.raw_file.path.startswith("sources/transcripts/")
    assert proposal.meeting_date == "2026-07-09"
    # Fixture SCHEMA.md has no transcript template: exactly two fields.
    frontmatter = proposal.raw_file.content.split("---")[1]
    fields = [line.split(":")[0] for line in frontmatter.strip().splitlines()]
    assert fields == ["created", "meeting_date"]
    assert "meeting_date: '2026-07-09'" in proposal.raw_file.content


def test_capture_writes_source_and_run(workspace, transcript):
    source_id = _capture(workspace, transcript, context="Attendees: Jane Doe, Bob.")

    with open_session(workspace) as session:
        source = session.get(Source, source_id)
        assert source.status == "raw"
        assert "Jane Doe" in source.metadata_json
        assert "2026-07-09" in source.metadata_json  # meeting date recorded
        run = session.scalar(select(IngestRun))
        assert "capture" in run.metadata_json
        # Raw capture is indexed as a note.
        paths = set(session.scalars(select(Note.path)))
        assert source.raw_text_path in paths
        # No memories at capture time.
        assert session.scalar(select(Memory)) is None


def test_capture_uses_schema_template_when_present(workspace, transcript):
    (workspace.root_path / "SCHEMA.md").write_text(
        "# Schema\n\n## Transcripts\n\nFiles in sources/transcripts use:\n\n"
        "```yaml\ntype: source\norigin: transcript\ntitle: \ndate: \ncreated: \n```\n"
    )
    proposal = prepare_capture(workspace, "transcript", file=transcript)

    frontmatter = proposal.raw_file.content.split("---")[1]
    fields = [line.split(":")[0] for line in frontmatter.strip().splitlines()]
    assert fields == ["type", "origin", "title", "date", "created"]
    assert "type: source" in frontmatter  # template value kept
    assert "date: '2026-07-09'" in frontmatter  # meeting date filled into `date`


def test_transcript_frontmatter_template_absent(workspace):
    # Fixture SCHEMA.md exists but has no yaml template.
    assert transcript_frontmatter_template(workspace) is None


def test_capture_duplicate_detected(workspace, transcript):
    _capture(workspace, transcript)
    second = prepare_capture(workspace, "transcript", file=transcript)
    assert second.duplicate_of is not None
    with pytest.raises(IngestError, match="already ingested"):
        apply_capture(workspace, second)


def test_capture_cleans_transcript(workspace, kb_path):
    noisy = kb_path / "noisy.txt"
    noisy.write_text("[00:00:01] Jane: hello\n00:00:05 Bob: hi\n")
    proposal = prepare_capture(workspace, "transcript", file=noisy)
    assert "Jane: hello\nBob: hi" in proposal.raw_file.content
    assert "[00:00:01]" not in proposal.raw_file.content


# --------------------------------------------------------------------------
# Enrichment


def test_enrichment_analyzes_and_links(workspace, transcript):
    source_id = _capture(workspace, transcript, context="Attendees: Jane Doe (Acme).")
    client = FakeClient()

    proposal = prepare_enrichment(workspace, source_id, client)

    # Capture-time context is reused and entity notes surface as candidates.
    prompt = client.prompts[0]
    assert "Jane Doe (Acme)" in prompt
    assert any(hit.ref == "people/jane-doe.md" for hit in proposal.related_notes)
    # The raw capture itself is not offered as a related note.
    assert all("sources/transcripts" not in hit.ref for hit in proposal.related_notes)
    assert proposal.title == "Claims Kickoff Meeting"
    assert len(proposal.memories) == 2
    assert proposal.proposed_note.path == "meetings/2026/2026-07-09-claims-kickoff.md"

    result = apply_enrichment(workspace, proposal)
    root = workspace.root_path
    assert (root / "meetings/2026/2026-07-09-claims-kickoff.md").exists()
    assert result.memories_created == 2
    assert result.relationships_created == 1

    with open_session(workspace) as session:
        source = session.get(Source, source_id)
        assert source.status == "enriched"
        assert source.title == "Claims Kickoff Meeting"
        memories = list(session.scalars(select(Memory)))
        assert all(m.state == "candidate" and m.source_id == source_id for m in memories)
        assert session.scalar(select(Relationship)) is not None


def test_enrichment_requires_existing_source(workspace):
    with pytest.raises(IngestError, match="No source with id"):
        prepare_enrichment(workspace, 999, FakeClient())


def test_enrichment_refuses_double_run_without_force(workspace, transcript):
    source_id = _capture(workspace, transcript)
    apply_enrichment(workspace, prepare_enrichment(workspace, source_id, FakeClient()))

    with pytest.raises(IngestError, match="already enriched"):
        prepare_enrichment(workspace, source_id, FakeClient())
    # --force allows re-analysis.
    proposal = prepare_enrichment(workspace, source_id, FakeClient(), force=True)
    assert proposal.summary


def test_enrichment_unsafe_note_path_falls_back_to_drafts(workspace, transcript):
    source_id = _capture(workspace, transcript)
    payload = dict(MODEL_JSON, proposed_note={"path": "../escape.md", "markdown": "# Escape\n"})
    proposal = prepare_enrichment(workspace, source_id, FakeClient(payload))
    assert proposal.proposed_note.path.startswith("drafts/")


def test_enrichment_malformed_output_degrades(workspace, transcript):
    source_id = _capture(workspace, transcript)
    proposal = prepare_enrichment(workspace, source_id, FakeClient("not json at all"))
    assert proposal.summary == "not json at all"
    assert proposal.memories == []
    assert proposal.proposed_note is None
    apply_enrichment(workspace, proposal)  # still applicable


def test_enrichment_guides_reach_prompt(workspace, transcript):
    (workspace.root_path / "RESOLVER.md").write_text("# Resolver\n\nMeetings go in meetings/.\n")
    source_id = _capture(workspace, transcript)
    client = FakeClient()
    prepare_enrichment(workspace, source_id, client)

    prompt = client.prompts[0]
    assert "Workspace guidance from SCHEMA.md" in prompt
    assert "Workspace guidance from RESOLVER.md" in prompt
    # Frontmatter is stripped from the analyzed text.
    assert "meeting_date:" not in prompt


# --------------------------------------------------------------------------
# Helpers


def test_strip_srt():
    srt = (
        "1\n00:00:01,000 --> 00:00:03,000\nHello there.\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\nGeneral Kenobi.\n"
    )
    assert strip_srt(srt) == "Hello there.\nGeneral Kenobi."


def test_clean_transcript_strips_timestamp_noise():
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


def test_infer_meeting_date():
    assert infer_meeting_date(Path("2026-07-09-standup.txt"), "") == "2026-07-09"
    assert infer_meeting_date(Path("standup-20260709.txt"), "") == "2026-07-09"
    assert infer_meeting_date(Path("standup.txt"), "Meeting on 2026-07-08\n...") == "2026-07-08"
    assert infer_meeting_date(Path("standup.txt"), "no date here") is None


def test_slugify():
    assert slugify("Claims Kickoff: Q3 / FNOL!") == "claims-kickoff-q3-fnol"
    assert slugify("   ") == "untitled"
