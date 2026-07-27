import json
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import frontmatter
import pytest
import yaml
from sqlalchemy import select

from wakil.app import ingest_service
from wakil.app.ingest_service import (
    EnrichmentProposal,
    EntityUpdate,
    IngestError,
    ProposedFile,
    _candidate_entity_notes,
    _dated_timeline_entry,
    _entity_attachment_dir,
    _is_noise_candidate,
    _is_unpopulated_stub,
    _merge_entity_note,
    _normalize_new_embed_paths,
    _require_workspace_ids,
    _split_candidates_by_content_length,
    _split_note_sections,
    _stub_content,
    _title_terms,
    apply_abstract_backfill,
    apply_capture,
    apply_enrichment,
    apply_entity_compile,
    clean_transcript,
    infer_meeting_date,
    parse_json_transcript,
    parse_whisper_transcript,
    plan_abstract_backfill,
    prepare_capture,
    prepare_enrichment,
    prepare_entity_compile,
    prepare_entity_full_resynthesis,
    slugify,
    strip_srt,
    transcript_frontmatter_template,
    validate_proposal,
)
from wakil.app.workspace_service import index_notes, init_workspace, open_session
from wakil.config.settings import WorkspaceConfig
from wakil.llm.client import ModelTruncatedError
from wakil.llm.schemas import EntityResolution, EntityRevision, ModelContractError
from wakil.schema.loader import load_entity_schemas
from wakil.storage.schema import IngestRun, Memory, Note, Relationship, Source

MODEL_JSON = {
    "title": "Claims Kickoff Meeting",
    "summary": "The team agreed to prototype FNOL routing using graph memory.",
    "key_points": ["Prototype approved", "Jane owns the routing design"],
    "memories": [
        {"type": "decision", "content": "Team will prototype FNOL routing.", "confidence": 0.9},
        {
            "type": "fact",
            "content": "Jane Doe owns the routing design.",
            "confidence": 0.8,
            "stance": "casual",
        },
        {
            "type": "event",
            "content": "Kickoff meeting for the FNOL routing prototype.",
            "confidence": 0.9,
            "event_date": "2026-07-09",
        },
    ],
    "relationships": [{"subject": 0, "predicate": "related_to", "object": 1}],
    "proposed_note": {
        "path": "meetings/2026/2026-07-09-claims-kickoff.md",
        "markdown": (
            "---\ntype: meeting\ntitle: Claims Kickoff\ndate: 2026-07-09\n"
            "created: 2026-07-09\n---\n\n"
            "# Claims Kickoff\n\nAttended by [[people/jane-doe.md]]. "
            "See [[concepts/claims-routing.md]].\n"
        ),
    },
}

RESOLUTION_JSON = {
    "entities": [
        {
            "name": "Dana Prieto",
            "entity_type": "person",
            "action": "create",
            "confidence": 0.85,
            "proposed_frontmatter": {"status": "active", "role": "Claims platform lead"},
        },
        {
            "name": "Jane Doe",
            "entity_type": "person",
            "action": "update",
            "target_note_path": "people/jane-doe.md",
            "confidence": 0.95,
            "proposed_frontmatter": {"role": "Routing design owner"},
        },
        {"name": "Acme", "entity_type": "company", "action": "skip", "confidence": 0.4},
    ]
}


REVISION_JSON = {"revisions": []}  # no-op: no entity update warrants a content change

CAPTURE_METADATA_JSON = {
    "title": "2026-07-09 Fake Capture Title",
    "abstract": "A fake, dense abstract used across capture tests -- roughly the length a real "
    "one would be, useful for retrieval without being a full summary of the source text.",
}


class FakeClient:
    """Scripted responses, one per model call (extraction, resolution, then
    entity-updates whenever a resolution's `update` target exists on disk)."""

    model = "fake-model"

    def __init__(self, payloads=None):
        if payloads is None:
            payloads = [MODEL_JSON, RESOLUTION_JSON, REVISION_JSON]
        self.queue = [json.dumps(p) if isinstance(p, dict) else p for p in payloads]
        self.calls: list[tuple[str, str]] = []

    def complete(self, system, prompt, max_tokens=8192, *, cacheable_prefix=None):
        self.calls.append((system, prompt))
        assert self.queue, "FakeClient ran out of scripted responses"
        step = self.queue.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


@pytest.fixture
def workspace(kb_path: Path) -> WorkspaceConfig:
    init_workspace(kb_path)
    return WorkspaceConfig.load(kb_path)


@pytest.fixture
def transcript(kb_path: Path) -> Path:
    path = kb_path / "2026-07-09-raw-meeting.txt"
    path.write_text("Jane: let's prototype FNOL routing with graph memory.\nBob: agreed.\n")
    return path


def _write_whisper(path: Path, transcripts: list[dict], date_created: float | None = None) -> Path:
    metadata = {
        "transcripts": transcripts,
        "speakers": [],
        "originalMediaFilename": "Voice Memo",
    }
    if date_created is not None:
        metadata["dateCreated"] = date_created
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("metadata.json", json.dumps(metadata))
        archive.writestr("originalAudio", b"")
    return path


def _segment(speaker: str, text: str, start: int) -> dict:
    return {"start": start, "end": start + 1000, "speaker": {"name": speaker}, "text": text}


def _write_json_transcript(path: Path, segments: list[dict]) -> Path:
    path.write_text(json.dumps({"segments": segments, "text": ""}))
    return path


def _json_segment(speaker: str, text: str, start: int) -> dict:
    # Same shape as _segment's "start"/"end"/"text", but a plain-string
    # "speaker" rather than a `{"name": ...}` object -- the one structural
    # difference from the .whisper archive format.
    return {"start": start, "end": start + 1000, "speaker": speaker, "text": text, "words": []}


def _capture_client(payload=None) -> FakeClient:
    """A capture-metadata-only fake: one scripted CaptureMetadata response."""
    return FakeClient([payload or CAPTURE_METADATA_JSON])


def _capture(workspace, transcript, context=None, client=None) -> int:
    proposal = prepare_capture(
        workspace, "transcript", client or _capture_client(), file=transcript, context=context
    )
    return apply_capture(workspace, proposal).source_id


# --------------------------------------------------------------------------
# Capture


def test_capture_fills_title_and_abstract_from_model(workspace, transcript):
    # A capture-metadata model call happens (docs/adr/0010); the schema
    # catalog (not a workspace SCHEMA.md) supplies the frontmatter shape,
    # and the model-provided title/abstract land in the known fields it
    # defines (docs/adr/0011).
    proposal = prepare_capture(workspace, "transcript", _capture_client(), file=transcript)

    assert proposal.raw_file.path.startswith("sources/transcripts/")
    assert proposal.meeting_date == "2026-07-09"
    assert proposal.title == CAPTURE_METADATA_JSON["title"]
    assert proposal.abstract == CAPTURE_METADATA_JSON["abstract"]
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


def test_transcript_frontmatter_template_from_schema_catalog(workspace):
    # Derived from schema/entities/source.yaml: base fields plus the
    # `transcript` origin sub-schema, in that order -- no workspace SCHEMA.md
    # involved.
    template = transcript_frontmatter_template(workspace)

    assert list(template) == [
        "type",
        "title",
        "origin",
        "url",
        "captured",
        "tags",
        "created",
        "abstract",
        "recording_url",
        "company",
        "meeting_date",
    ]
    assert template["type"] == "source"  # the schema's own type name, kept literal
    assert template["title"] == ""  # blank placeholder for fields with no known value


def test_capture_transcript_frontmatter_equivalent_to_old_schema_scrape(workspace, transcript):
    """Same effect as the retired SCHEMA.md yaml-block scrape for a
    representative transcript: type is kept, and every field the code
    catalog can fill (title, abstract, origin, url, dates) is filled with a
    real value rather than left as a placeholder. Title/abstract now come
    from the capture-time model call (docs/adr/0010), not the filename."""
    proposal = prepare_capture(workspace, "transcript", _capture_client(), file=transcript)
    today = datetime.now(UTC).date().isoformat()

    frontmatter = yaml.safe_load(proposal.raw_file.content.split("---")[1])
    assert frontmatter["type"] == "source"
    assert frontmatter["title"] == CAPTURE_METADATA_JSON["title"]
    assert frontmatter["abstract"] == CAPTURE_METADATA_JSON["abstract"]
    # "origin" is the enumerated kind, not a path; "url" is a KB-root-relative
    # file: reference, never the machine's absolute path.
    assert frontmatter["origin"] == "transcript"
    assert frontmatter["url"] == "file:2026-07-09-raw-meeting.txt"
    assert frontmatter["meeting_date"] == "2026-07-09"
    assert frontmatter["captured"] == today
    assert frontmatter["created"] == today
    # Fields the schema defines but this capture has no value for stay blank
    # placeholders rather than being invented or omitted.
    assert frontmatter["tags"] == ""
    assert frontmatter["company"] == ""
    assert frontmatter["recording_url"] == ""


def test_capture_origin_is_relative_to_kb_root(workspace, kb_path):
    nested = kb_path / "sources" / "audio"
    nested.mkdir(parents=True)
    file = nested / "call.txt"
    file.write_text("Ed: hi\n")
    proposal = prepare_capture(workspace, "transcript", _capture_client(), file=file)
    assert proposal.origin == "sources/audio/call.txt"


def test_capture_duplicate_detected(workspace, transcript):
    _capture(workspace, transcript)
    second = prepare_capture(workspace, "transcript", _capture_client(), file=transcript)
    assert second.duplicate_of is not None
    with pytest.raises(IngestError, match="already ingested"):
        apply_capture(workspace, second)


def test_capture_race_closes_via_unique_constraint(workspace, kb_path):
    """Two processes capturing identical content under different filenames
    both pass prepare_capture's check, since neither has committed yet --
    the second apply_capture must still catch it via the DB constraint
    rather than silently creating a second Source row for the same
    content."""
    content = "Jane: let's prototype FNOL routing with graph memory.\nBob: agreed.\n"
    first_file = kb_path / "meeting-one.txt"
    first_file.write_text(content)
    second_file = kb_path / "meeting-two.txt"
    second_file.write_text(content)

    first_proposal = prepare_capture(workspace, "transcript", _capture_client(), file=first_file)
    second_proposal = prepare_capture(workspace, "transcript", _capture_client(), file=second_file)
    assert first_proposal.duplicate_of is None
    assert second_proposal.duplicate_of is None  # race window: neither committed yet
    assert first_proposal.raw_file.path != second_proposal.raw_file.path

    first_result = apply_capture(workspace, first_proposal)

    second_target = workspace.root_path / second_proposal.raw_file.path
    with pytest.raises(IngestError, match="already ingested"):
        apply_capture(workspace, second_proposal)
    # The file the loser wrote before losing the race must not be left
    # behind as an orphan with no Source row.
    assert not second_target.exists()

    with open_session(workspace) as session:
        sources = list(
            session.scalars(
                select(Source).where(Source.content_hash == first_proposal.content_hash)
            )
        )
    assert len(sources) == 1
    assert sources[0].id == first_result.source_id


def test_capture_cleans_transcript(workspace, kb_path):
    noisy = kb_path / "noisy.txt"
    noisy.write_text("[00:00:01] Jane: hello\n00:00:05 Bob: hi\n")
    proposal = prepare_capture(workspace, "transcript", _capture_client(), file=noisy)
    assert "Jane: hello\nBob: hi" in proposal.raw_file.content
    assert "[00:00:01]" not in proposal.raw_file.content


def test_capture_slug_strips_leading_date_from_filename(workspace, kb_path):
    # The raw file's path/slug is derived from the filename and must stay
    # fully deterministic (docs/adr/0010) -- unaffected by whatever title
    # the model returns, which is asserted separately below.
    dated = kb_path / "2026-07-16-mosaic-eleni-karahalios.txt"
    dated.write_text("Ed: hi\n")
    proposal = prepare_capture(workspace, "transcript", _capture_client(), file=dated)
    assert proposal.raw_file.path == "sources/transcripts/2026-07-16-mosaic-eleni-karahalios.md"
    assert proposal.title == CAPTURE_METADATA_JSON["title"]


def test_capture_adds_h1_matching_the_destination_filename(workspace, transcript):
    proposal = prepare_capture(workspace, "transcript", _capture_client(), file=transcript)
    body = proposal.raw_file.content.split("---", 2)[2].lstrip("\n")
    assert body.startswith("# 2026-07-09-raw-meeting\n\n")


def test_capture_transcript_whisper(workspace, kb_path):
    whisper = _write_whisper(
        kb_path / "2026-07-16-mosaic-eleni-karahalios.whisper",
        [
            _segment("Edward Bridges", "Hi, this is Ed.", 0),
            _segment("Eleni Karahalios", "Hey Ed, this is Eleni.", 1000),
            _segment("Eleni Karahalios", "How are you?", 2000),
            _segment("Edward Bridges", "I'm glad we managed to uh work through it.", 3000),
        ],
    )
    proposal = prepare_capture(workspace, "transcript", _capture_client(), file=whisper)

    assert proposal.meeting_date == "2026-07-16"  # from the filename, not dateCreated
    assert (
        "**Edward Bridges**: Hi, this is Ed.\n\n"
        "**Eleni Karahalios**: Hey Ed, this is Eleni. How are you?\n\n"
        "**Edward Bridges**: I'm glad we managed to work through it."
    ) in proposal.raw_file.content
    body = proposal.raw_file.content.split("---", 2)[2].lstrip("\n")
    assert body.startswith("# 2026-07-16-mosaic-eleni-karahalios\n\n")


def test_capture_transcript_whisper_falls_back_to_recorded_at(workspace, kb_path):
    # No date in the filename, so meeting_date must come from the archive's
    # own dateCreated (seconds since the 2001-01-01 reference epoch).
    whisper = _write_whisper(
        kb_path / "call.whisper",
        [_segment("Ed", "hello", 0)],
        date_created=805926332.753,  # 2026-07-16
    )
    proposal = prepare_capture(workspace, "transcript", _capture_client(), file=whisper)
    assert proposal.meeting_date == "2026-07-16"


def test_capture_transcript_whisper_rejects_non_zip(workspace, kb_path):
    bad = kb_path / "broken.whisper"
    bad.write_bytes(b"not a zip file")
    with pytest.raises(IngestError, match="not a valid whisper archive"):
        prepare_capture(workspace, "transcript", _capture_client(), file=bad)


def test_capture_transcript_whisper_rejects_missing_metadata(workspace, kb_path):
    empty = kb_path / "empty.whisper"
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("originalAudio", b"")
    with pytest.raises(IngestError, match="no metadata.json"):
        prepare_capture(workspace, "transcript", _capture_client(), file=empty)


def test_capture_transcript_json_segments(workspace, kb_path):
    # A plain-JSON transcript export (e.g. faster-whisper/WhisperKit-style
    # "segments" array with flat "speaker" strings) -- structurally close to
    # but not the same as the .whisper zip archive format above.
    json_file = _write_json_transcript(
        kb_path / "2026-07-16-mosaic-eleni-karahalios.json",
        [
            _json_segment("Edward Bridges", "Hi, this is Ed.", 0),
            _json_segment("Eleni Karahalios", "Hey Ed, this is Eleni.", 1000),
            _json_segment("Eleni Karahalios", "How are you?", 2000),
            _json_segment("Edward Bridges", "I'm glad we managed to uh work through it.", 3000),
        ],
    )
    proposal = prepare_capture(workspace, "transcript", _capture_client(), file=json_file)

    assert proposal.meeting_date == "2026-07-16"  # from the filename
    assert (
        "**Edward Bridges**: Hi, this is Ed.\n\n"
        "**Eleni Karahalios**: Hey Ed, this is Eleni. How are you?\n\n"
        "**Edward Bridges**: I'm glad we managed to work through it."
    ) in proposal.raw_file.content
    body = proposal.raw_file.content.split("---", 2)[2].lstrip("\n")
    assert body.startswith("# 2026-07-16-mosaic-eleni-karahalios\n\n")


def test_capture_transcript_json_rejects_missing_segments(workspace, kb_path):
    bad = kb_path / "broken.json"
    bad.write_text(json.dumps({"text": "no segments here"}))
    with pytest.raises(IngestError, match="no `segments` array"):
        prepare_capture(workspace, "transcript", _capture_client(), file=bad)


def test_capture_transcript_json_rejects_invalid_json(workspace, kb_path):
    bad = kb_path / "broken2.json"
    bad.write_text("not json at all")
    with pytest.raises(IngestError, match="Could not read JSON transcript"):
        prepare_capture(workspace, "transcript", _capture_client(), file=bad)


def test_parse_json_transcript_strips_filler_words_only(kb_path):
    json_file = _write_json_transcript(
        kb_path / "sample.json",
        [_json_segment("Jane", "I um I was calling you, uh, about the offer.", 0)],
    )
    dialogue = parse_json_transcript(json_file)
    assert dialogue == "**Jane**: I I was calling you, about the offer."


def test_capture_uses_model_generated_title_and_abstract(workspace, transcript):
    # Frontmatter title/abstract come from the model (docs/adr/0010); the
    # raw file's slug does not, and is checked separately elsewhere.
    client = _capture_client()
    proposal = prepare_capture(workspace, "transcript", client, file=transcript)

    assert len(client.calls) == 1
    _, prompt = client.calls[0]
    assert "Prefixed with the date of the ingest" in prompt
    assert "under 60 characters" in prompt
    assert "NOT a sentence" in prompt
    assert "NOT generic" in prompt
    assert "roughly 300 characters" in prompt
    assert proposal.title == CAPTURE_METADATA_JSON["title"]
    assert proposal.abstract == CAPTURE_METADATA_JSON["abstract"]


def test_capture_writes_title_and_abstract_when_schema_template_has_them(workspace, transcript):
    (workspace.root_path / "SCHEMA.md").write_text(
        "# Schema\n\n## Transcripts\n\nFiles in sources/transcripts use:\n\n"
        "```yaml\ntype: source\ntitle: \nabstract: \ndate: \ncreated: \n```\n"
    )
    proposal = prepare_capture(workspace, "transcript", _capture_client(), file=transcript)

    frontmatter = proposal.raw_file.content.split("---")[1]
    assert f"title: {CAPTURE_METADATA_JSON['title']}" in frontmatter
    assert "abstract:" in frontmatter


def test_capture_persists_title_and_abstract_to_source_metadata(workspace, transcript):
    source_id = _capture(workspace, transcript)
    with open_session(workspace) as session:
        source = session.get(Source, source_id)
        metadata = json.loads(source.metadata_json)
        assert metadata["title"] == CAPTURE_METADATA_JSON["title"]
        assert metadata["abstract"] == CAPTURE_METADATA_JSON["abstract"]
        assert source.title == CAPTURE_METADATA_JSON["title"]


def test_parse_whisper_transcript_strips_filler_words_only(kb_path):
    whisper = _write_whisper(
        kb_path / "sample.whisper",
        [_segment("Jane", "I um I was calling you, uh, about the offer.", 0)],
    )
    dialogue, _ = parse_whisper_transcript(whisper)
    # Only the isolated filler tokens are removed; the repeated "I" and the
    # rest of the phrasing are left exactly as spoken (no ASR repair).
    assert dialogue == "**Jane**: I I was calling you, about the offer."


# --------------------------------------------------------------------------
# Enrichment


def test_enrichment_analyzes_and_links(workspace, transcript):
    source_id = _capture(workspace, transcript, context="Attendees: Jane Doe (Acme).")
    client = FakeClient()

    proposal = prepare_enrichment(workspace, source_id, client)

    # Three model calls: extraction, entity resolution (always invoked),
    # then entity-updates — triggered here because RESOLUTION_JSON resolves
    # Jane Doe to action=update against the fixture's real people/jane-doe.md.
    assert len(client.calls) == 3
    extraction_system, extraction_prompt = client.calls[0]
    resolution_system, resolution_prompt = client.calls[1]
    revision_system, revision_prompt = client.calls[2]
    assert "Find the resolution, not the first option" in extraction_system
    assert '"ExtractionOutput"' in extraction_system  # contract schema injected
    assert "Jane Doe (Acme)" in extraction_prompt
    assert "entity-resolution step" in resolution_system
    assert "Known entity types" in resolution_prompt
    # The revision call's system prompt is note-revision/SKILL.md itself,
    # and it's given the target's full current content, per "read the
    # existing note in full before writing anything."
    assert "note-revision" in revision_system
    assert "Works on claims automation" in revision_prompt  # jane-doe.md's own body

    # The full field catalog (required + optional) for every entity type is
    # in the extraction prompt unconditionally — it's rendered structurally
    # from wakil's own schema catalog (load_entity_schemas), not workspace
    # prose.
    assert "meeting (directory: meetings" in extraction_prompt
    assert "decisions (optional, list)" in extraction_prompt
    assert "action-items (optional, list)" in extraction_prompt
    # Each type names its page_shape, and the matching template body is
    # rendered once — a meeting is single-occurrence (no Timeline), a
    # person is compiled-truth-timeline (no Key Decisions/Action Items).
    assert "page_shape: single-occurrence" in extraction_prompt
    assert "page_shape: compiled-truth-timeline" in extraction_prompt
    assert "Page shape 'single-occurrence'" in extraction_prompt
    assert "Page shape 'compiled-truth-timeline'" in extraction_prompt
    assert "Timeline / Log" in extraction_prompt
    # `type` names a schema rather than being one of its fields, so it never
    # appears in the rendered field catalog — the model needs to be told
    # explicitly to still write it as its own frontmatter line.
    assert "`type: <name>`" in extraction_prompt
    assert "person" in resolution_prompt

    assert any(hit.ref == "people/jane-doe.md" for hit in proposal.related_notes)
    # The raw capture itself is not offered as a related note.
    assert all("sources/transcripts" not in hit.ref for hit in proposal.related_notes)
    assert proposal.title == "Claims Kickoff Meeting"
    assert len(proposal.memories) == 3
    assert proposal.proposed_note.path == "meetings/2026/2026-07-09-claims-kickoff.md"
    # Resolution results: one stub for the new person, none for update/skip.
    assert [r.action for r in proposal.entity_resolutions] == ["create", "update", "skip"]
    assert [stub.path for stub in proposal.stub_entities] == ["people/dana-prieto.md"]
    assert validate_proposal(proposal) == []

    result = apply_enrichment(workspace, proposal)
    root = workspace.root_path
    assert (root / "meetings/2026/2026-07-09-claims-kickoff.md").exists()
    assert (root / "people/dana-prieto.md").exists()
    assert result.files_written == [
        "meetings/2026/2026-07-09-claims-kickoff.md",
        "people/dana-prieto.md",
    ]
    assert result.memories_created == 3
    assert result.relationships_created == 1

    with open_session(workspace) as session:
        source = session.get(Source, source_id)
        assert source.status == "enriched"
        assert source.title == "Claims Kickoff Meeting"
        memories = list(session.scalars(select(Memory)))
        assert all(m.state == "candidate" and m.source_id == source_id for m in memories)
        # The dated event carries its own date for Timeline ordering.
        event = next(m for m in memories if m.memory_type == "event")
        assert event.event_date == date(2026, 7, 9)
        # stance flows from the extraction JSON through to the Memory row,
        # independent of memory_type (docs/adr/0014).
        fact = next(m for m in memories if m.memory_type == "fact")
        assert fact.stance == "casual"
        decision = next(m for m in memories if m.memory_type == "decision")
        assert decision.stance is None
        assert session.scalar(select(Relationship)) is not None


def test_reconcile_corrects_note_link_to_match_entity_resolution(workspace, transcript):
    # Extraction and entity resolution are independent model calls that can
    # disagree about which existing page an entity name refers to: here
    # extraction's prose links to the wrong "Mosaic" page while entity
    # resolution correctly resolves to the other one. The reconciliation
    # pass must rewrite the note's link to match entity-resolution's answer.
    source_id = _capture(workspace, transcript)
    payload = dict(
        MODEL_JSON,
        proposed_note={
            "path": "meetings/2026/2026-07-09-claims-kickoff.md",
            "markdown": (
                "---\ntype: meeting\ntitle: Claims Kickoff\ndate: 2026-07-09\n"
                "created: 2026-07-09\n---\n\n"
                "# Claims Kickoff\n\nDiscussed [[companies/mosaic-app|Mosaic]] deal terms.\n"
            ),
        },
    )
    resolution = {
        "entities": RESOLUTION_JSON["entities"]
        + [
            {
                "name": "Mosaic",
                "entity_type": "company",
                "action": "update",
                "target_note_path": "companies/mosaic-private-markets.md",
                "confidence": 0.9,
            }
        ]
    }

    proposal = prepare_enrichment(
        workspace, source_id, FakeClient([payload, resolution, REVISION_JSON])
    )

    assert "[[companies/mosaic-private-markets.md|Mosaic]]" in proposal.proposed_note.content
    assert "[[companies/mosaic-app|Mosaic]]" not in proposal.proposed_note.content
    assert any(
        "Corrected 1 entity link" in warning
        and "companies/mosaic-app|Mosaic" in warning
        and "companies/mosaic-private-markets.md|Mosaic" in warning
        for warning in proposal.warnings
    )


def test_reconcile_leaves_already_matching_link_untouched(workspace, transcript):
    source_id = _capture(workspace, transcript)
    markdown = (
        "---\ntype: meeting\ntitle: Claims Kickoff\ndate: 2026-07-09\n"
        "created: 2026-07-09\n---\n\n"
        "# Claims Kickoff\n\nDiscussed [[companies/mosaic-private-markets.md|Mosaic]] terms.\n"
    )
    payload = dict(
        MODEL_JSON,
        proposed_note={"path": "meetings/2026/2026-07-09-claims-kickoff.md", "markdown": markdown},
    )
    resolution = {
        "entities": RESOLUTION_JSON["entities"]
        + [
            {
                "name": "Mosaic",
                "entity_type": "company",
                "action": "update",
                "target_note_path": "companies/mosaic-private-markets.md",
                "confidence": 0.9,
            }
        ]
    }

    proposal = prepare_enrichment(
        workspace, source_id, FakeClient([payload, resolution, REVISION_JSON])
    )

    assert proposal.proposed_note.content == markdown
    assert not any("Corrected" in warning for warning in proposal.warnings)


def test_reconcile_ignores_md_suffix_when_comparing_the_same_target(workspace, transcript):
    # This KB's own wikilinks mix conventions: extraction wrote the link
    # without ".md" (matching how entity pages are linked elsewhere in this
    # vault), entity-resolution's target_note_path has it (matching
    # Note.path). Same page either way — must not be treated as a mismatch.
    source_id = _capture(workspace, transcript)
    markdown = (
        "---\ntype: meeting\ntitle: Claims Kickoff\ndate: 2026-07-09\n"
        "created: 2026-07-09\n---\n\n"
        "# Claims Kickoff\n\nDiscussed [[companies/mosaic-private-markets|Mosaic]] terms.\n"
    )
    payload = dict(
        MODEL_JSON,
        proposed_note={"path": "meetings/2026/2026-07-09-claims-kickoff.md", "markdown": markdown},
    )
    resolution = {
        "entities": RESOLUTION_JSON["entities"]
        + [
            {
                "name": "Mosaic",
                "entity_type": "company",
                "action": "update",
                "target_note_path": "companies/mosaic-private-markets.md",
                "confidence": 0.9,
            }
        ]
    }

    proposal = prepare_enrichment(
        workspace, source_id, FakeClient([payload, resolution, REVISION_JSON])
    )

    assert proposal.proposed_note.content == markdown
    assert not any("Corrected" in warning for warning in proposal.warnings)


def test_reconcile_does_not_touch_unresolved_display_text(workspace, transcript):
    # A wikilink whose display text doesn't match any entity-resolution name
    # is left alone — this is a conservative, exact-match-only fix, not
    # fuzzy/alias guessing.
    source_id = _capture(workspace, transcript)
    markdown = (
        "---\ntype: meeting\ntitle: Claims Kickoff\ndate: 2026-07-09\n"
        "created: 2026-07-09\n---\n\n"
        "# Claims Kickoff\n\nSee [[companies/mosaic-app|Some Other Thing]] for background.\n"
    )
    payload = dict(
        MODEL_JSON,
        proposed_note={"path": "meetings/2026/2026-07-09-claims-kickoff.md", "markdown": markdown},
    )
    resolution = {
        "entities": RESOLUTION_JSON["entities"]
        + [
            {
                "name": "Mosaic",
                "entity_type": "company",
                "action": "update",
                "target_note_path": "companies/mosaic-private-markets.md",
                "confidence": 0.9,
            }
        ]
    }

    proposal = prepare_enrichment(
        workspace, source_id, FakeClient([payload, resolution, REVISION_JSON])
    )

    assert proposal.proposed_note.content == markdown
    assert not any("Corrected" in warning for warning in proposal.warnings)


# --------------------------------------------------------------------------
# Entity updates (DAG node 3) — the only place wakil edits an existing file.
# REAL_SHAPED_PERSON mirrors actual entity pages in production use, not an
# idealized one: no explicit "## Compiled Truth" heading (the top section is
# just prose after the H1), and a trailing block of auto-generated back-link
# bullets after the dated entries that must never be touched.

REAL_SHAPED_PERSON = (
    "---\n"
    "type: person\n"
    "name: Priya Shah\n"
    "status: active\n"
    "tags:\n  - job-search\n"
    "created: 2026-06-01\n"
    "updated: 2026-06-01\n"
    "---\n\n"
    "# priya-shah\n\n"
    "**Recruiter** at [[companies/acme|Acme]]. First screen 2026-06-01.\n\n"
    "Cross-references: [[companies/acme|Acme]]\n\n"
    "---\n\n"
    "## Timeline / Log\n\n"
    "### 2026-06-01 — recruiter screen\n"
    "- Introductory call, discussed the VP Eng role.\n\n"
    "- **2026-06-01** | Referenced in [some-meeting](meetings/2026/2026-06-01-screen.md)\n"
)


def test_split_note_sections_splits_real_shaped_person(workspace):
    body = frontmatter.loads(REAL_SHAPED_PERSON).content
    result = _split_note_sections(body)

    assert result is not None
    h1_line, top_section, timeline_section = result
    assert h1_line == "# priya-shah"
    assert "**Recruiter** at [[companies/acme|Acme]]. First screen 2026-06-01." in top_section
    # The "---" divider right before Timeline is not part of the top section.
    assert "---" not in top_section
    assert timeline_section.startswith("## Timeline / Log")
    assert "### 2026-06-01 — recruiter screen" in timeline_section
    assert "- **2026-06-01** | Referenced in [some-meeting]" in timeline_section


def test_split_note_sections_returns_none_for_missing_timeline_heading(workspace):
    # Mirrors _merge_entity_note's own shape-mismatch fixture: an H1 with no
    # "## Timeline / Log" heading at all.
    minimal = "---\ntype: person\nname: Jane Doe\n---\n\n# Jane Doe\n\nWorks on claims.\n"
    body = frontmatter.loads(minimal).content
    assert _split_note_sections(body) is None


def test_split_note_sections_returns_none_for_missing_h1(workspace):
    no_h1 = (
        "---\ntype: person\nname: Jane Doe\n---\n\nWorks on claims.\n\n## Timeline / Log\n\nstuff\n"
    )
    body = frontmatter.loads(no_h1).content
    assert _split_note_sections(body) is None


def test_merge_entity_note_replaces_top_section_preserves_h1_and_prepends_timeline(workspace):
    revision = EntityRevision(
        target_note_path="people/priya-shah.md",
        has_update=True,
        compiled_truth="**Recruiter** at [[companies/acme|Acme]]. Now running a second search.\n\n"
        "Cross-references: [[companies/acme|Acme]]",
        timeline_entry="### 2026-07-16 — second search kicked off\n- New role, same recruiter.",
    )
    new_content = _merge_entity_note(REAL_SHAPED_PERSON, revision, "2026-07-16")

    assert new_content is not None
    assert "# priya-shah\n\n**Recruiter** at [[companies/acme|Acme]]. Now running" in new_content
    # Old top-section prose is gone, replaced, not appended alongside.
    assert "First screen 2026-06-01" not in new_content
    # New timeline entry comes first...
    lines = new_content.splitlines()
    new_idx = next(i for i, line in enumerate(lines) if "second search kicked off" in line)
    old_idx = next(i for i, line in enumerate(lines) if "recruiter screen" in line)
    assert new_idx < old_idx
    # ...but the old entry and the trailing auto-generated back-link bullet
    # both survive, verbatim, untouched.
    assert "### 2026-06-01 — recruiter screen" in new_content
    assert "- Introductory call, discussed the VP Eng role." in new_content
    assert "- **2026-06-01** | Referenced in [some-meeting]" in new_content
    # updated: bumped to today; created: left alone.
    assert "updated: '2026-07-16'" in new_content or "updated: 2026-07-16" in new_content
    assert "created: 2026-06-01" in new_content


def test_merge_entity_note_dumps_created_and_updated_in_same_unquoted_form(workspace):
    # Regression test for #78: metadata["updated"] used to be assigned as a
    # plain str, which yaml.safe_dump quotes to disambiguate from a real
    # date scalar (created: 2026-06-01 vs. updated: '2026-07-16'), even
    # though both fields represent the same kind of value. Both should
    # round-trip to the same unquoted plain-scalar form.
    revision = EntityRevision(
        target_note_path="people/priya-shah.md",
        has_update=True,
        compiled_truth="**Recruiter** at [[companies/acme|Acme]]. Now running a second search.",
        timeline_entry="### 2026-07-16 — second search kicked off\n- New role, same recruiter.",
    )
    new_content = _merge_entity_note(REAL_SHAPED_PERSON, revision, "2026-07-16")

    assert new_content is not None
    frontmatter_yaml = new_content.split("---\n", 2)[1]
    parsed = yaml.safe_load(frontmatter_yaml)
    assert parsed["created"] == date(2026, 6, 1)
    assert parsed["updated"] == date(2026, 7, 16)
    # Neither field is quoted in the dumped YAML text.
    assert "created: '2026-06-01'" not in new_content
    assert "updated: '2026-07-16'" not in new_content
    assert "created: 2026-06-01" in new_content
    assert "updated: 2026-07-16" in new_content


def test_merge_entity_note_stamps_updated_even_when_missing_from_original(workspace):
    # people/edward-bridges.md's real shape: no "updated" key at all, only
    # "created". Required by schema on every merge target's entity type
    # (person/company/concept/project all require it), so it must be added,
    # not left permanently missing just because it wasn't there before.
    no_updated_field = REAL_SHAPED_PERSON.replace("updated: 2026-06-01\n", "")
    revision = EntityRevision(
        target_note_path="people/priya-shah.md",
        has_update=True,
        compiled_truth="Updated truth.",
        timeline_entry="### 2026-07-16 — note\n- detail",
    )
    new_content = _merge_entity_note(no_updated_field, revision, "2026-07-16")

    assert new_content is not None
    assert "updated: '2026-07-16'" in new_content or "updated: 2026-07-16" in new_content


REAL_SHAPED_PERSON_WITH_COMPILED_TRUTH_HEADING = (
    "---\n"
    "type: company\n"
    "name: Acme\n"
    "created: 2026-06-01\n"
    "updated: 2026-06-01\n"
    "---\n\n"
    "# acme\n\n"
    "## Compiled Truth\n\n"
    "AI-native widgets. Long-established prior analysis that must survive "
    "any update where the model doesn't resend it.\n\n"
    "Cross-references: [[people/priya-shah|Priya Shah]]\n\n"
    "---\n\n"
    "## Timeline / Log\n\n"
    "### 2026-06-01 — recruiter screen\n"
    "- Introductory call.\n\n"
    "- **2026-06-01** | Referenced in [some-meeting](meetings/2026/2026-06-01-screen.md)\n"
)


def test_merge_entity_note_preserves_top_section_when_compiled_truth_omitted(workspace):
    # has_update=True can legitimately mean "only the Timeline changed" — an
    # empty/absent compiled_truth must never be treated as "delete the
    # Compiled Truth section." Regression for the clobbering bug hit against
    # the real companies/mosaic-private-markets.md, which has an explicit
    # "## Compiled Truth" heading (unlike REAL_SHAPED_PERSON's headingless
    # top section).
    revision = EntityRevision(
        target_note_path="companies/acme.md",
        has_update=True,
        compiled_truth=None,
        timeline_entry="### 2026-07-16 — new development\n- Something happened.",
    )
    new_content = _merge_entity_note(
        REAL_SHAPED_PERSON_WITH_COMPILED_TRUTH_HEADING, revision, "2026-07-16"
    )

    assert new_content is not None
    assert "## Compiled Truth" in new_content
    assert "Long-established prior analysis that must survive" in new_content
    assert "Cross-references: [[people/priya-shah|Priya Shah]]" in new_content
    # New timeline entry lands, old ones survive.
    assert "### 2026-07-16 — new development" in new_content
    assert "### 2026-06-01 — recruiter screen" in new_content
    # No duplicated "---" divider from stitching old_top back in.
    assert "---\n\n---" not in new_content
    assert "updated: '2026-07-16'" in new_content or "updated: 2026-07-16" in new_content


def test_merge_entity_note_only_changes_specified_frontmatter_fields(workspace):
    revision = EntityRevision(
        target_note_path="people/priya-shah.md",
        has_update=True,
        compiled_truth="Updated truth.",
        timeline_entry="### 2026-07-16 — note\n- detail",
        frontmatter_updates={"status": "former"},
    )
    new_content = _merge_entity_note(REAL_SHAPED_PERSON, revision, "2026-07-16")

    assert "status: former" in new_content
    assert "name: Priya Shah" in new_content  # untouched field survives
    assert "- job-search" in new_content  # untouched list field survives


def test_merge_entity_note_accepts_bare_timeline_heading_without_log_suffix(workspace):
    # Predates the "## Timeline / Log" convention (e.g. real people/edward-bridges.md) —
    # must still be recognized, not silently skipped.
    legacy_shaped = REAL_SHAPED_PERSON.replace("## Timeline / Log", "## Timeline")
    revision = EntityRevision(
        target_note_path="people/priya-shah.md",
        has_update=True,
        compiled_truth="Updated truth.",
        timeline_entry="### 2026-07-16 — note\n- detail",
    )
    new_content = _merge_entity_note(legacy_shaped, revision, "2026-07-16")

    assert new_content is not None
    assert "Updated truth." in new_content
    assert "### 2026-07-16 — note" in new_content
    assert "### 2026-06-01 — recruiter screen" in new_content


def test_merge_entity_note_returns_none_for_unexpected_shape(workspace):
    # No "## Timeline / Log" heading at all — the real people/jane-doe.md
    # fixture shape. The caller must not guess a different structure.
    minimal = "---\ntype: person\nname: Jane Doe\n---\n\n# Jane Doe\n\nWorks on claims.\n"
    revision = EntityRevision(
        target_note_path="people/jane-doe.md",
        has_update=True,
        compiled_truth="Something",
        timeline_entry="### 2026-07-16 — x\n- y",
    )
    assert _merge_entity_note(minimal, revision, "2026-07-16") is None


# --------------------------------------------------------------------------
# Issue #76: embed path normalization on the update/merge path


def test_entity_attachment_dir_is_sibling_named_after_note_file():
    assert _entity_attachment_dir("projects/pencil-box.md") == "projects/pencil-box"
    # No parent directory: still just the bare stem, no leading "./" cruft.
    assert _entity_attachment_dir("pencil-box.md") == "pencil-box"


def test_merge_entity_note_normalizes_new_bare_filename_embed_to_vault_root_absolute(workspace):
    # The reproduction in issue #76: the model writes a newly-introduced
    # embed as a bare filename (or some other verbatim, non-vault-rooted
    # path) rather than an absolute path from the vault root.
    revision = EntityRevision(
        target_note_path="people/priya-shah.md",
        has_update=True,
        compiled_truth="Updated truth. ![[box-1.jpg|Build photo]]",
        timeline_entry="### 2026-07-16 — new photos\n- New build photo.",
    )
    new_content = _merge_entity_note(REAL_SHAPED_PERSON, revision, "2026-07-16")

    assert new_content is not None
    assert "![[people/priya-shah/box-1.jpg|Build photo]]" in new_content
    assert "![[box-1.jpg|Build photo]]" not in new_content


def test_merge_entity_note_normalizes_new_embed_introduced_via_timeline_entry(workspace):
    # Same defect, but the new reference lands via the Timeline entry rather
    # than the re-synthesized Compiled Truth -- both must be normalized.
    revision = EntityRevision(
        target_note_path="people/priya-shah.md",
        has_update=True,
        compiled_truth=None,
        timeline_entry="### 2026-07-16 — new photos\n- See ![[photos/box-2.jpg]].",
    )
    new_content = _merge_entity_note(REAL_SHAPED_PERSON, revision, "2026-07-16")

    assert new_content is not None
    assert "![[people/priya-shah/box-2.jpg]]" in new_content
    assert "![[photos/box-2.jpg]]" not in new_content


def test_merge_entity_note_leaves_preexisting_embed_untouched(workspace):
    # An embed already on the page (carried forward verbatim per
    # note-revision/SKILL.md's attachment-fidelity duty) is left exactly as
    # it was -- only a genuinely *new* reference gets normalized. Also
    # covers the case where the pre-existing embed is deliberately NOT
    # already vault-root-absolute: this function's job is only to fix
    # newly-introduced references, not to retroactively repair old ones.
    existing = REAL_SHAPED_PERSON.replace(
        "**Recruiter** at [[companies/acme|Acme]].",
        "**Recruiter** at [[companies/acme|Acme]]. ![[legacy/odd-path.jpg|Old photo]]",
    )
    revision = EntityRevision(
        target_note_path="people/priya-shah.md",
        has_update=True,
        # The model re-sends the pre-existing embed verbatim (as it must)
        # alongside one genuinely new reference.
        compiled_truth="**Recruiter** at [[companies/acme|Acme]]. "
        "![[legacy/odd-path.jpg|Old photo]] ![[box-1.jpg|New photo]]",
        timeline_entry="### 2026-07-16 — update\n- detail",
    )
    new_content = _merge_entity_note(existing, revision, "2026-07-16")

    assert new_content is not None
    # Pre-existing embed: untouched, not "fixed" to the sibling convention.
    assert "![[legacy/odd-path.jpg|Old photo]]" in new_content
    # Newly-introduced embed: normalized.
    assert "![[people/priya-shah/box-1.jpg|New photo]]" in new_content
    assert "![[box-1.jpg|New photo]]" not in new_content


def test_merge_entity_note_no_attachment_references_is_unaffected(workspace):
    # No-regression case: an update with no `![[...]]` embeds at all
    # (the common case) merges exactly as before -- and, notably, a plain
    # `[[wikilink]]` (no leading "!") is never mistaken for an embed and
    # rewritten into the attachment folder.
    revision = EntityRevision(
        target_note_path="people/priya-shah.md",
        has_update=True,
        compiled_truth="**Recruiter** at [[companies/acme|Acme]]. Now running a second search.",
        timeline_entry="### 2026-07-16 — second search kicked off\n- New role, same recruiter.",
    )
    new_content = _merge_entity_note(REAL_SHAPED_PERSON, revision, "2026-07-16")

    assert new_content is not None
    # Plain wikilink survives verbatim -- never rewritten into the
    # attachment-folder convention, which only applies to `![[...]]` embeds.
    assert "[[companies/acme|Acme]]" in new_content
    assert "priya-shah/" not in new_content


def test_normalize_new_embed_paths_ignores_external_urls():
    old_content = "no embeds here"
    text = "See ![[https://example.com/photo.jpg|External]] for context."
    result = _normalize_new_embed_paths(old_content, text, "people/priya-shah.md")
    assert result == text


def test_entity_update_normalizes_new_embed_path_end_to_end(workspace, transcript, kb_path):
    # Full prepare_enrichment path (issue #76's actual reproduction shape):
    # a merge introduces a new attachment reference and the resulting
    # EntityUpdate.new_content must carry the vault-root-absolute form, not
    # whatever verbatim path the model wrote.
    _write_person(kb_path, "priya-shah")
    resolution = {
        "entities": [
            {
                "name": "Priya Shah",
                "entity_type": "person",
                "action": "update",
                "target_note_path": "people/priya-shah.md",
                "confidence": 0.9,
            }
        ]
    }
    revisions = {
        "revisions": [
            {
                "target_note_path": "people/priya-shah.md",
                "has_update": True,
                "compiled_truth": "New synthesized truth. ![[box-1.jpg|Build photo]]",
                "timeline_entry": "### 2026-07-16 — new info\n- detail",
            }
        ]
    }
    source_id = _capture(workspace, transcript)
    proposal = prepare_enrichment(
        workspace, source_id, FakeClient([MODEL_JSON, resolution, revisions])
    )

    assert len(proposal.entity_updates) == 1
    update = proposal.entity_updates[0]
    assert "![[people/priya-shah/box-1.jpg|Build photo]]" in update.new_content
    assert "![[box-1.jpg|Build photo]]" not in update.new_content


# --------------------------------------------------------------------------
# issue #77: a placeholder Timeline heading (no real date of its own) must
# fall back to the source's own captured/retrieved date rather than being
# written verbatim into the append-only Timeline.


def test_dated_timeline_entry_leaves_real_date_heading_unchanged():
    entry = "### 2026-06-01 — recruiter screen\n- Introductory call."
    assert _dated_timeline_entry(entry, "2026-07-20") == entry


def test_dated_timeline_entry_is_noop_without_fallback_date():
    entry = "### (date not recorded)\n- Something happened."
    assert _dated_timeline_entry(entry, None) == entry


def test_dated_timeline_entry_substitutes_fallback_for_parenthetical_placeholder():
    entry = "### (date not recorded) — merged into existing entity\n- Something happened."
    result = _dated_timeline_entry(entry, "2026-07-20")
    assert result == "### 2026-07-20 — merged into existing entity\n- Something happened."


def test_dated_timeline_entry_substitutes_fallback_for_undated_source_heading():
    # Confirmed real-world regression: merging an unrelated follow-up
    # source produced this exact heading.
    entry = "### Undated -- source: clipping\n- Some detail."
    result = _dated_timeline_entry(entry, "2026-07-20")
    assert result == "### 2026-07-20 — source: clipping\n- Some detail."


def test_dated_timeline_entry_substitutes_bare_placeholder_with_no_description():
    entry = "### undated\n- Some detail."
    result = _dated_timeline_entry(entry, "2026-07-20")
    assert result == "### 2026-07-20\n- Some detail."


def test_merge_entity_note_substitutes_fallback_date_for_placeholder_heading(workspace):
    revision = EntityRevision(
        target_note_path="people/priya-shah.md",
        has_update=True,
        compiled_truth="Updated truth.",
        timeline_entry="### (date not recorded) — merged into existing entity\n- detail",
    )
    new_content = _merge_entity_note(REAL_SHAPED_PERSON, revision, "2026-07-16", "2026-07-15")

    assert new_content is not None
    assert "(date not recorded)" not in new_content
    assert "### 2026-07-15 — merged into existing entity" in new_content
    # Existing, real-dated entry survives untouched.
    assert "### 2026-06-01 — recruiter screen" in new_content


def test_merge_entity_note_leaves_real_date_heading_unchanged_even_with_fallback(workspace):
    revision = EntityRevision(
        target_note_path="people/priya-shah.md",
        has_update=True,
        compiled_truth="Updated truth.",
        timeline_entry="### 2026-07-16 — second search kicked off\n- New role.",
    )
    new_content = _merge_entity_note(REAL_SHAPED_PERSON, revision, "2026-07-16", "2026-07-01")

    assert new_content is not None
    assert "### 2026-07-16 — second search kicked off" in new_content
    assert "2026-07-01" not in new_content


def test_is_unpopulated_stub_true_for_fresh_stub_content():
    stub = _stub_content({"type": "person", "name": "Priya Shah"}, "Priya Shah")
    assert _is_unpopulated_stub(stub) is True


def test_is_unpopulated_stub_false_for_real_shaped_person():
    assert _is_unpopulated_stub(REAL_SHAPED_PERSON) is False


def test_is_unpopulated_stub_false_for_malformed_content():
    assert _is_unpopulated_stub("not a note at all") is False


def _write_person(kb_path: Path, slug: str, content: str = REAL_SHAPED_PERSON) -> None:
    people = kb_path / "people"
    people.mkdir(exist_ok=True)
    (people / f"{slug}.md").write_text(content.replace("priya-shah", slug))


def _write_stub_person(kb_path: Path, slug: str, name: str = "Priya Shah") -> None:
    """Write a person page using the actual `_stub_content` skeleton —
    i.e. an entity that's never had real content synthesized into it,
    exactly as `_build_stub_entities` leaves it at creation time."""
    people = kb_path / "people"
    people.mkdir(exist_ok=True)
    metadata = {
        "type": "person",
        "name": name,
        "status": "active",
        "created": "2026-06-01",
        "updated": "2026-06-01",
    }
    (people / f"{slug}.md").write_text(ingest_service._stub_content(metadata, name))


def test_entity_update_applies_when_model_says_has_update(workspace, transcript, kb_path):
    _write_person(kb_path, "priya-shah")
    resolution = {
        "entities": [
            {
                "name": "Priya Shah",
                "entity_type": "person",
                "action": "update",
                "target_note_path": "people/priya-shah.md",
                "confidence": 0.9,
            }
        ]
    }
    revisions = {
        "revisions": [
            {
                "target_note_path": "people/priya-shah.md",
                "has_update": True,
                "compiled_truth": "New synthesized truth.",
                "timeline_entry": "### 2026-07-16 — new info\n- detail",
            }
        ]
    }
    source_id = _capture(workspace, transcript)
    proposal = prepare_enrichment(
        workspace, source_id, FakeClient([MODEL_JSON, resolution, revisions])
    )

    assert len(proposal.entity_updates) == 1
    update = proposal.entity_updates[0]
    assert update.target_note_path == "people/priya-shah.md"
    assert "New synthesized truth." in update.new_content
    assert "new info" in update.new_content

    result = apply_enrichment(workspace, proposal)
    assert "people/priya-shah.md" in result.files_written
    on_disk = (workspace.root_path / "people/priya-shah.md").read_text()
    assert "New synthesized truth." in on_disk


def test_entity_update_carries_low_confidence_through_to_proposal(workspace, transcript, kb_path):
    # A field value inferred from thin evidence (issue #39) must be
    # distinguishable downstream, not merged in looking exactly as
    # confident as a well-supported one.
    _write_person(kb_path, "priya-shah")
    resolution = {
        "entities": [
            {
                "name": "Priya Shah",
                "entity_type": "person",
                "action": "update",
                "target_note_path": "people/priya-shah.md",
                "confidence": 0.9,
            }
        ]
    }
    revisions = {
        "revisions": [
            {
                "target_note_path": "people/priya-shah.md",
                "has_update": True,
                "compiled_truth": "Tentative truth from a single thin mention.",
                "frontmatter_updates": {"status": "former"},
                "confidence": 0.2,
            }
        ]
    }
    source_id = _capture(workspace, transcript)
    proposal = prepare_enrichment(
        workspace, source_id, FakeClient([MODEL_JSON, resolution, revisions])
    )

    assert len(proposal.entity_updates) == 1
    update = proposal.entity_updates[0]
    assert update.confidence == 0.2


def test_entity_update_skipped_when_model_says_has_update_false(workspace, transcript, kb_path):
    _write_person(kb_path, "priya-shah")
    resolution = {
        "entities": [
            {
                "name": "Priya Shah",
                "entity_type": "person",
                "action": "update",
                "target_note_path": "people/priya-shah.md",
                "confidence": 0.9,
            }
        ]
    }
    revisions = {"revisions": [{"target_note_path": "people/priya-shah.md", "has_update": False}]}
    source_id = _capture(workspace, transcript)
    proposal = prepare_enrichment(
        workspace, source_id, FakeClient([MODEL_JSON, resolution, revisions])
    )

    assert proposal.entity_updates == []
    # REAL_SHAPED_PERSON already has real content — declining to touch it
    # further is unremarkable, not a founding-content-loss signal.
    assert not any("empty stub" in warning for warning in proposal.warnings)


def test_entity_update_declined_on_still_stub_entity_warns_founding_content_may_be_lost(
    workspace, transcript, kb_path
):
    # Issue #45: the entity was created as a bare _stub_content skeleton and
    # has never been populated. This update pass looks at it and declines to
    # add anything (has_update=False) — same as any other declined update,
    # except here it means the entity's founding facts may never land.
    _write_stub_person(kb_path, "priya-shah")
    resolution = {
        "entities": [
            {
                "name": "Priya Shah",
                "entity_type": "person",
                "action": "update",
                "target_note_path": "people/priya-shah.md",
                "confidence": 0.9,
            }
        ]
    }
    revisions = {"revisions": [{"target_note_path": "people/priya-shah.md", "has_update": False}]}
    source_id = _capture(workspace, transcript)
    proposal = prepare_enrichment(
        workspace, source_id, FakeClient([MODEL_JSON, resolution, revisions])
    )

    assert proposal.entity_updates == []
    assert any(
        "Priya Shah" in warning and "empty stub" in warning and "permanently missing" in warning
        for warning in proposal.warnings
    )


def test_entity_update_below_relevance_threshold_on_still_stub_entity_also_warns(
    workspace, transcript, kb_path
):
    # Same failure shape as above, but the entity never even reaches the
    # revision call because it's filtered out by the relevance gate first.
    _write_stub_person(kb_path, "priya-shah")
    resolution = {
        "entities": [
            {
                "name": "Priya Shah",
                "entity_type": "person",
                "action": "update",
                "target_note_path": "people/priya-shah.md",
                "confidence": 0.9,
                "relevance": "minor",
            }
        ]
    }
    source_id = _capture(workspace, transcript)
    client = FakeClient([MODEL_JSON, resolution])  # no 3rd response needed/consumed
    proposal = prepare_enrichment(workspace, source_id, client)

    assert proposal.entity_updates == []
    assert any(
        "Priya Shah" in warning and "empty stub" in warning and "permanently missing" in warning
        for warning in proposal.warnings
    )


def test_entity_update_skipped_for_single_occurrence_shape_type(workspace, transcript, kb_path):
    # meeting is single-occurrence — note-revision's State/Timeline
    # discipline doesn't define what "update" means for it, so it's never
    # even offered to the revision call.
    meetings = kb_path / "meetings"
    meetings.mkdir(exist_ok=True)
    (meetings / "past-sync.md").write_text(
        "---\ntype: meeting\ntitle: Past Sync\ndate: 2026-06-01\ncreated: 2026-06-01\n---\n\n"
        "# past-sync\n\n## Summary\n\nOld content.\n"
    )
    resolution = {
        "entities": [
            {
                "name": "Past Sync",
                "entity_type": "meeting",
                "action": "update",
                "target_note_path": "meetings/past-sync.md",
                "confidence": 0.9,
            }
        ]
    }
    source_id = _capture(workspace, transcript)
    client = FakeClient([MODEL_JSON, resolution])  # no 3rd response needed/consumed
    proposal = prepare_enrichment(workspace, source_id, client)

    assert proposal.entity_updates == []
    assert len(client.calls) == 2


def test_entity_update_warns_when_target_missing_on_disk(workspace, transcript):
    resolution = {
        "entities": [
            {
                "name": "Ghost Person",
                "entity_type": "person",
                "action": "update",
                "target_note_path": "people/ghost-person.md",
                "confidence": 0.9,
            }
        ]
    }
    source_id = _capture(workspace, transcript)
    client = FakeClient([MODEL_JSON, resolution])  # no 3rd response needed/consumed
    proposal = prepare_enrichment(workspace, source_id, client)

    assert proposal.entity_updates == []
    assert any("doesn't exist on disk" in warning for warning in proposal.warnings)
    assert len(client.calls) == 2


# --------------------------------------------------------------------------
# Issue #36: duplicate entity stubs (create-resolutions whose subject
# already has a home in this same proposal).


def test_stub_suppressed_when_matches_proposed_note_subject(workspace, transcript):
    # MODEL_JSON's proposed_note is a `meeting` page titled "Claims Kickoff".
    # Entity resolution independently proposing a `create` for the exact
    # same subject under a different type (here `project`) must not produce
    # a second, always-empty page for it — but a genuinely distinct new
    # entity (Dana Prieto) in the same resolution call still gets its stub.
    resolution = {
        "entities": [
            {
                "name": "Claims Kickoff",
                "entity_type": "project",
                "action": "create",
                "confidence": 0.8,
            },
            {
                "name": "Dana Prieto",
                "entity_type": "person",
                "action": "create",
                "confidence": 0.85,
            },
        ]
    }
    source_id = _capture(workspace, transcript)
    client = FakeClient([MODEL_JSON, resolution])  # no update actions -> no 3rd call
    proposal = prepare_enrichment(workspace, source_id, client)

    assert [stub.path for stub in proposal.stub_entities] == ["people/dana-prieto.md"]
    assert any(
        "Claims Kickoff" in warning and "already represented by the proposed note" in warning
        for warning in proposal.warnings
    )
    assert len(client.calls) == 2


def test_stub_suppressed_when_matches_applied_entity_update_target(
    workspace, transcript, kb_path
):
    # Entity resolution proposes both a real update to an existing long-lived
    # entity (people/priya-shah.md) AND, independently, a `create` for the
    # same subject under a different (builtin) type -- the worst-cases in
    # issue #36 look like journal/meeting duplicates alongside a project
    # entity's own correct Timeline update. The create must be suppressed
    # once the update is confirmed to actually change content.
    _write_person(kb_path, "priya-shah")
    resolution = {
        "entities": [
            {
                "name": "Priya Shah",
                "entity_type": "person",
                "action": "update",
                "target_note_path": "people/priya-shah.md",
                "confidence": 0.9,
                "relevance": "central",
            },
            {
                "name": "Priya Shah",
                "entity_type": "journal",
                "action": "create",
                "confidence": 0.7,
            },
        ]
    }
    revisions = {
        "revisions": [
            {
                "target_note_path": "people/priya-shah.md",
                "has_update": True,
                "compiled_truth": "New synthesized truth.",
                "timeline_entry": "### 2026-07-16 — new info\n- detail",
            }
        ]
    }
    source_id = _capture(workspace, transcript)
    proposal = prepare_enrichment(
        workspace, source_id, FakeClient([MODEL_JSON, resolution, revisions])
    )

    assert len(proposal.entity_updates) == 1
    assert proposal.stub_entities == []
    assert any(
        "subject already updated via an existing entity" in warning
        for warning in proposal.warnings
    )


def test_stub_kept_when_create_subject_differs_from_update_target(
    workspace, transcript, kb_path
):
    # No over-suppression: a create-resolution for a genuinely distinct
    # entity must still get its stub even when the same proposal also
    # updates an unrelated existing entity.
    _write_person(kb_path, "priya-shah")
    resolution = {
        "entities": [
            {
                "name": "Priya Shah",
                "entity_type": "person",
                "action": "update",
                "target_note_path": "people/priya-shah.md",
                "confidence": 0.9,
                "relevance": "central",
            },
            {
                "name": "Dana Prieto",
                "entity_type": "person",
                "action": "create",
                "confidence": 0.85,
            },
        ]
    }
    revisions = {
        "revisions": [
            {
                "target_note_path": "people/priya-shah.md",
                "has_update": True,
                "compiled_truth": "New synthesized truth.",
                "timeline_entry": "### 2026-07-16 — new info\n- detail",
            }
        ]
    }
    source_id = _capture(workspace, transcript)
    proposal = prepare_enrichment(
        workspace, source_id, FakeClient([MODEL_JSON, resolution, revisions])
    )

    assert len(proposal.entity_updates) == 1
    assert [stub.path for stub in proposal.stub_entities] == ["people/dana-prieto.md"]
    assert not any(
        "subject already updated via an existing entity" in warning
        for warning in proposal.warnings
    )


# --------------------------------------------------------------------------
# Issue #60: dated journal/meeting stubs are redundant once this same
# source has already merged into an existing accumulating entity via an
# update in this same proposal -- even though the dated record's own
# name/slug (a date/topic) never matches the update target's slug, so
# _suppress_stubs_matching_updates's own slug-matching can't catch it.


def test_dated_record_stub_suppressed_when_source_already_merged_via_update(
    workspace, transcript, kb_path
):
    # Priya Shah's page gets a real Timeline update, but entity resolution
    # ALSO independently proposes a `journal` create whose name/slug is a
    # date+topic entirely unrelated to "priya-shah" -- exactly the shape
    # _suppress_stubs_matching_updates cannot catch (issue #60). It must
    # still be suppressed, because this same source already has a home.
    _write_person(kb_path, "priya-shah")
    resolution = {
        "entities": [
            {
                "name": "Priya Shah",
                "entity_type": "person",
                "action": "update",
                "target_note_path": "people/priya-shah.md",
                "confidence": 0.9,
                "relevance": "central",
            },
            {
                "name": "2014-05-17 Elektrum VPC Subnet Layout Finalized",
                "entity_type": "journal",
                "action": "create",
                "confidence": 0.7,
            },
        ]
    }
    revisions = {
        "revisions": [
            {
                "target_note_path": "people/priya-shah.md",
                "has_update": True,
                "compiled_truth": "New synthesized truth.",
                "timeline_entry": "### 2026-07-16 — new info\n- detail",
            }
        ]
    }
    source_id = _capture(workspace, transcript)
    proposal = prepare_enrichment(
        workspace, source_id, FakeClient([MODEL_JSON, resolution, revisions])
    )

    assert len(proposal.entity_updates) == 1
    assert proposal.stub_entities == []
    assert any(
        "journal/2014-05-17-elektrum-vpc-subnet-layout-finalized.md" in warning
        and "already merged into an existing entity" in warning
        for warning in proposal.warnings
    )


def test_dated_record_stub_kept_when_no_entity_updates_applied(workspace, transcript, kb_path):
    # No regression: a `journal` create with no entity_updates at all in
    # this proposal (a genuinely new, unrelated record) must still get its
    # stub -- the new suppression only fires alongside an applied update.
    resolution = {
        "entities": [
            {
                "name": "2014-05-17 Elektrum VPC Subnet Layout Finalized",
                "entity_type": "journal",
                "action": "create",
                "confidence": 0.7,
            },
        ]
    }
    source_id = _capture(workspace, transcript)
    client = FakeClient([MODEL_JSON, resolution])  # no update actions -> no 3rd call
    proposal = prepare_enrichment(workspace, source_id, client)

    assert proposal.entity_updates == []
    assert [stub.path for stub in proposal.stub_entities] == [
        "journal/2014-05-17-elektrum-vpc-subnet-layout-finalized.md"
    ]
    assert not any(
        "already merged into an existing entity" in warning for warning in proposal.warnings
    )


def test_stub_kept_for_type_outside_narrow_dated_record_scope(workspace, transcript, kb_path):
    # No over-suppression: a create for a type NOT in the narrow
    # journal/meeting scope (a legitimate new hobby-project, say) must keep
    # its stub even when this same proposal also applies an unrelated
    # entity update -- the fix is deliberately scoped to journal/meeting,
    # not every type that could share a source with an update.
    #
    # proposed_note is overridden away from MODEL_JSON's default `meeting`
    # type so this test isolates stub suppression -- with the default
    # `meeting` proposed_note, _suppress_proposed_note_matching_updates
    # (issue #68) would also legitimately null it out here, which is
    # covered by its own tests below rather than this one.
    _write_person(kb_path, "priya-shah")
    payload = dict(
        MODEL_JSON,
        proposed_note={
            "path": "concepts/handler-assignment.md",
            "markdown": (
                "---\ntype: concept\nname: Handler Assignment\n"
                "created: 2026-07-09\nupdated: 2026-07-09\n---\n\n"
                "# Handler Assignment\n\nBackground on FNOL routing.\n"
            ),
        },
    )
    resolution = {
        "entities": [
            {
                "name": "Priya Shah",
                "entity_type": "person",
                "action": "update",
                "target_note_path": "people/priya-shah.md",
                "confidence": 0.9,
                "relevance": "central",
            },
            {
                "name": "Elektrum",
                "entity_type": "project",
                "action": "create",
                "confidence": 0.8,
            },
        ]
    }
    revisions = {
        "revisions": [
            {
                "target_note_path": "people/priya-shah.md",
                "has_update": True,
                "compiled_truth": "New synthesized truth.",
                "timeline_entry": "### 2026-07-16 — new info\n- detail",
            }
        ]
    }
    source_id = _capture(workspace, transcript)
    proposal = prepare_enrichment(
        workspace, source_id, FakeClient([payload, resolution, revisions])
    )

    assert len(proposal.entity_updates) == 1
    assert [stub.path for stub in proposal.stub_entities] == ["projects/elektrum.md"]
    assert proposal.proposed_note is not None
    assert not any(
        "already merged into an existing entity" in warning for warning in proposal.warnings
    )


# --------------------------------------------------------------------------
# Issue #68: _suppress_stubs_matching_updates and
# _suppress_dated_record_stubs_matching_updates only ever prune
# proposal.stub_entities -- neither ever inspects proposal.proposed_note,
# which is set by extraction *before* entity resolution runs. A dated
# source can correctly update an existing entity's Timeline via
# entity_updates and *also* independently produce its own proposed_note
# for the same source; nothing suppressed that duplicate.


def test_proposed_note_suppressed_when_subject_matches_applied_entity_update(
    workspace, transcript, kb_path
):
    # MODEL_JSON's proposed_note is titled "Claims Kickoff" (slug
    # claims-kickoff). An existing entity at that same slug gets a real
    # Timeline update in this same proposal -- the note is just extraction's
    # own independent representation of a subject that already has a home.
    _write_person(kb_path, "claims-kickoff")
    resolution = {
        "entities": [
            {
                "name": "Claims Kickoff",
                "entity_type": "person",
                "action": "update",
                "target_note_path": "people/claims-kickoff.md",
                "confidence": 0.9,
                "relevance": "central",
            }
        ]
    }
    revisions = {
        "revisions": [
            {
                "target_note_path": "people/claims-kickoff.md",
                "has_update": True,
                "compiled_truth": "New synthesized truth.",
                "timeline_entry": "### 2026-07-16 — new info\n- detail",
            }
        ]
    }
    source_id = _capture(workspace, transcript)
    proposal = prepare_enrichment(
        workspace, source_id, FakeClient([MODEL_JSON, resolution, revisions])
    )

    assert len(proposal.entity_updates) == 1
    assert proposal.proposed_note is None
    assert any(
        "subject already updated via an existing entity" in warning
        for warning in proposal.warnings
    )


def test_proposed_note_suppressed_when_dated_record_type_and_source_already_merged(
    workspace, transcript, kb_path
):
    # MODEL_JSON's proposed_note is a `meeting` page titled "Claims Kickoff"
    # -- a subject entirely unrelated to "priya-shah", so the subject-slug
    # match above cannot catch it. It must still be suppressed: a
    # journal/meeting note's whole purpose is "record what this source
    # said," which the Priya Shah Timeline update -- from this same source,
    # in this same proposal -- has already done.
    _write_person(kb_path, "priya-shah")
    resolution = {
        "entities": [
            {
                "name": "Priya Shah",
                "entity_type": "person",
                "action": "update",
                "target_note_path": "people/priya-shah.md",
                "confidence": 0.9,
                "relevance": "central",
            }
        ]
    }
    revisions = {
        "revisions": [
            {
                "target_note_path": "people/priya-shah.md",
                "has_update": True,
                "compiled_truth": "New synthesized truth.",
                "timeline_entry": "### 2026-07-16 — new info\n- detail",
            }
        ]
    }
    source_id = _capture(workspace, transcript)
    proposal = prepare_enrichment(
        workspace, source_id, FakeClient([MODEL_JSON, resolution, revisions])
    )

    assert len(proposal.entity_updates) == 1
    assert proposal.proposed_note is None
    assert any(
        "meetings/2026/2026-07-09-claims-kickoff.md" in warning
        and "already merged into an existing entity" in warning
        for warning in proposal.warnings
    )


def test_proposed_note_kept_when_unrelated_to_any_entity_update(workspace, transcript, kb_path):
    # No over-suppression: a proposed_note whose type is outside the narrow
    # journal/meeting scope, and whose subject doesn't match any applied
    # update's target, must survive even though this same proposal also
    # applies an unrelated entity update.
    _write_person(kb_path, "priya-shah")
    payload = dict(
        MODEL_JSON,
        proposed_note={
            "path": "concepts/handler-assignment.md",
            "markdown": (
                "---\ntype: concept\nname: Handler Assignment\n"
                "created: 2026-07-09\nupdated: 2026-07-09\n---\n\n"
                "# Handler Assignment\n\nBackground on FNOL routing.\n"
            ),
        },
    )
    resolution = {
        "entities": [
            {
                "name": "Priya Shah",
                "entity_type": "person",
                "action": "update",
                "target_note_path": "people/priya-shah.md",
                "confidence": 0.9,
                "relevance": "central",
            }
        ]
    }
    revisions = {
        "revisions": [
            {
                "target_note_path": "people/priya-shah.md",
                "has_update": True,
                "compiled_truth": "New synthesized truth.",
                "timeline_entry": "### 2026-07-16 — new info\n- detail",
            }
        ]
    }
    source_id = _capture(workspace, transcript)
    proposal = prepare_enrichment(
        workspace, source_id, FakeClient([payload, resolution, revisions])
    )

    assert len(proposal.entity_updates) == 1
    assert proposal.proposed_note is not None
    assert proposal.proposed_note.path == "concepts/handler-assignment.md"
    assert not any(
        "already updated via an existing entity" in warning
        or "already merged into an existing entity" in warning
        for warning in proposal.warnings
    )


@pytest.mark.parametrize("relevance", ["minor", "peripheral"])
def test_entity_update_excluded_when_relevance_below_threshold(
    workspace, transcript, kb_path, relevance
):
    _write_person(kb_path, "priya-shah")
    resolution = {
        "entities": [
            {
                "name": "Priya Shah",
                "entity_type": "person",
                "action": "update",
                "target_note_path": "people/priya-shah.md",
                "confidence": 0.9,
                "relevance": relevance,
            }
        ]
    }
    source_id = _capture(workspace, transcript)
    client = FakeClient([MODEL_JSON, resolution])  # no 3rd response needed/consumed
    proposal = prepare_enrichment(workspace, source_id, client)

    assert proposal.entity_updates == []
    assert any(
        "below the relevance threshold" in warning and "Priya Shah" in warning
        for warning in proposal.warnings
    )
    assert len(client.calls) == 2


@pytest.mark.parametrize("relevance", ["central", "notable", None])
def test_entity_update_proceeds_when_relevance_at_or_above_threshold(
    workspace, transcript, kb_path, relevance
):
    _write_person(kb_path, "priya-shah")
    resolution = {
        "entities": [
            {
                "name": "Priya Shah",
                "entity_type": "person",
                "action": "update",
                "target_note_path": "people/priya-shah.md",
                "confidence": 0.9,
                "relevance": relevance,
            }
        ]
    }
    revisions = {
        "revisions": [
            {
                "target_note_path": "people/priya-shah.md",
                "has_update": True,
                "compiled_truth": "New synthesized truth.",
                "timeline_entry": "### 2026-07-16 — new info\n- detail",
            }
        ]
    }
    source_id = _capture(workspace, transcript)
    proposal = prepare_enrichment(
        workspace, source_id, FakeClient([MODEL_JSON, resolution, revisions])
    )

    assert len(proposal.entity_updates) == 1
    assert not any("below the relevance threshold" in warning for warning in proposal.warnings)


def _candidate(name: str, content_len: int):
    resolution = EntityResolution(name=name, entity_type="person", action="update")
    return (resolution, Path(f"people/{name}.md"), "x" * content_len)


def test_split_by_content_length_isolates_largest_into_its_own_half():
    candidates = [_candidate("a", 10), _candidate("b", 5), _candidate("c", 100), _candidate("d", 3)]
    half_a, half_b = _split_candidates_by_content_length(candidates)

    names_a = {c[0].name for c in half_a}
    names_b = {c[0].name for c in half_b}
    assert names_a | names_b == {"a", "b", "c", "d"}
    assert names_a & names_b == set()
    assert names_a == {"c"}  # the single largest, alone
    assert names_b == {"a", "b", "d"}


def test_split_by_content_length_balances_total_size_not_count():
    candidates = [_candidate("a", 100), _candidate("b", 1), _candidate("c", 1), _candidate("d", 1)]
    half_a, half_b = _split_candidates_by_content_length(candidates)

    assert sum(len(c[2]) for c in half_a) == 100
    assert sum(len(c[2]) for c in half_b) == 3


def test_split_by_content_length_two_candidates_splits_one_each():
    half_a, half_b = _split_candidates_by_content_length([_candidate("a", 10), _candidate("b", 5)])
    assert len(half_a) == 1
    assert len(half_b) == 1


# A note shaped like REAL_SHAPED_PERSON but padded to be dramatically larger
# than a typical target note — for exercising which half of a bisection
# split a large note lands in.
LONG_SHAPED_PERSON = (
    "---\n"
    "type: person\n"
    "name: Alice Long\n"
    "status: active\n"
    "created: 2026-06-01\n"
    "updated: 2026-06-01\n"
    "---\n\n"
    "# alice\n\n"
    + ("Filler paragraph to inflate this note's existing content length. " * 100)
    + "\n\n---\n\n"
    "## Timeline / Log\n\n"
    "### 2026-06-01 — history\n"
    "- Some prior detail.\n"
)


def test_entity_updates_bisect_and_recover_after_truncation(workspace, transcript, kb_path):
    _write_person(kb_path, "alice", content=LONG_SHAPED_PERSON)
    _write_person(kb_path, "bob")
    _write_person(kb_path, "carol")
    resolution = {
        "entities": [
            {
                "name": slug.title(),
                "entity_type": "person",
                "action": "update",
                "target_note_path": f"people/{slug}.md",
                "confidence": 0.9,
                "relevance": "central",
            }
            for slug in ("alice", "bob", "carol")
        ]
    }
    alice_revision = {
        "revisions": [
            {
                "target_note_path": "people/alice.md",
                "has_update": True,
                "compiled_truth": "Alice updated truth.",
                "timeline_entry": "### 2026-07-16 — alice update\n- detail",
            }
        ]
    }
    bob_carol_revision = {
        "revisions": [
            {
                "target_note_path": "people/bob.md",
                "has_update": True,
                "compiled_truth": "Bob updated truth.",
                "timeline_entry": "### 2026-07-16 — bob update\n- detail",
            },
            {
                "target_note_path": "people/carol.md",
                "has_update": True,
                "compiled_truth": "Carol updated truth.",
                "timeline_entry": "### 2026-07-16 — carol update\n- detail",
            },
        ]
    }
    source_id = _capture(workspace, transcript)
    client = FakeClient(
        [
            MODEL_JSON,
            resolution,
            # First (unsplit) attempt truncates twice -> triggers a split.
            ModelTruncatedError(max_tokens=8192, partial="{"),
            ModelTruncatedError(max_tokens=16384, partial="{"),
            # alice, isolated as the largest note, succeeds alone.
            alice_revision,
            # bob + carol, the smaller remainder, succeed together.
            bob_carol_revision,
        ]
    )

    proposal = prepare_enrichment(workspace, source_id, client)

    assert {u.target_note_path for u in proposal.entity_updates} == {
        "people/alice.md",
        "people/bob.md",
        "people/carol.md",
    }
    assert not any("failed while revising" in warning for warning in proposal.warnings)
    assert len(client.calls) == 6


def test_entity_updates_stop_bisecting_at_depth_ceiling(
    workspace, transcript, kb_path, monkeypatch
):
    monkeypatch.setattr(ingest_service, "_MAX_BISECTION_DEPTH", 1)
    _write_person(kb_path, "dan")
    _write_person(kb_path, "erin")
    resolution = {
        "entities": [
            {
                "name": slug.title(),
                "entity_type": "person",
                "action": "update",
                "target_note_path": f"people/{slug}.md",
                "confidence": 0.9,
                "relevance": "central",
            }
            for slug in ("dan", "erin")
        ]
    }
    source_id = _capture(workspace, transcript)
    # Every revision attempt truncates, at every depth: 2 (top) + 2 (each of
    # the 2 leaf sub-batches once split) = 6 truncations after extraction
    # and resolution.
    client = FakeClient(
        [MODEL_JSON, resolution]
        + [ModelTruncatedError(max_tokens=8192, partial="{") for _ in range(6)]
    )

    proposal = prepare_enrichment(workspace, source_id, client)

    assert proposal.entity_updates == []
    failure_warnings = [w for w in proposal.warnings if "failed while revising" in w]
    # Split once (depth 0 -> 1), then the ceiling stops further splitting:
    # two separate single-entity failures, not one two-entity failure and
    # not unbounded further recursion.
    assert len(failure_warnings) == 2
    assert any("Dan" in w for w in failure_warnings)
    assert any("Erin" in w for w in failure_warnings)
    assert len(client.calls) == 8


def test_entity_updates_validation_failure_does_not_trigger_split(workspace, transcript, kb_path):
    _write_person(kb_path, "dan")
    _write_person(kb_path, "erin")
    resolution = {
        "entities": [
            {
                "name": slug.title(),
                "entity_type": "person",
                "action": "update",
                "target_note_path": f"people/{slug}.md",
                "confidence": 0.9,
                "relevance": "central",
            }
            for slug in ("dan", "erin")
        ]
    }
    source_id = _capture(workspace, transcript)
    client = FakeClient([MODEL_JSON, resolution, "not json", "still not json"])

    proposal = prepare_enrichment(workspace, source_id, client)

    assert proposal.entity_updates == []
    failure_warnings = [w for w in proposal.warnings if "failed while revising" in w]
    assert len(failure_warnings) == 1
    assert "2 entities" in failure_warnings[0]
    assert len(client.calls) == 4


def test_apply_enrichment_skips_stale_entity_update_without_clobbering(
    workspace, transcript, kb_path
):
    _write_person(kb_path, "priya-shah")
    resolution = {
        "entities": [
            {
                "name": "Priya Shah",
                "entity_type": "person",
                "action": "update",
                "target_note_path": "people/priya-shah.md",
                "confidence": 0.9,
            }
        ]
    }
    revisions = {
        "revisions": [
            {
                "target_note_path": "people/priya-shah.md",
                "has_update": True,
                "compiled_truth": "New synthesized truth.",
                "timeline_entry": "### 2026-07-16 — new info\n- detail",
            }
        ]
    }
    source_id = _capture(workspace, transcript)
    proposal = prepare_enrichment(
        workspace, source_id, FakeClient([MODEL_JSON, resolution, revisions])
    )

    # The file changes on disk after prepare, before apply.
    target = workspace.root_path / "people/priya-shah.md"
    hand_edit = target.read_text() + "\n<!-- user's own concurrent edit -->\n"
    target.write_text(hand_edit)

    result = apply_enrichment(workspace, proposal)

    assert "people/priya-shah.md" not in result.files_written
    assert any("changed on disk" in msg for msg in result.stale_updates_skipped)
    # Never clobbered — the user's concurrent edit is exactly what's on disk.
    assert target.read_text() == hand_edit


# --------------------------------------------------------------------------
# Entity compile pilot (docs/adr/0016): `wakil entities compile SLUG`.


def test_prepare_and_apply_entity_compile_happy_path(workspace, kb_path):
    _write_person(kb_path, "priya-shah")
    compiled_truth = (
        "**Recruiter** at [[companies/acme|Acme]]. Now running a second search "
        "for the same VP Eng role."
    )
    client = FakeClient([{"compiled_truth": compiled_truth}])

    update = prepare_entity_compile(workspace, client, "priya-shah")

    assert len(client.calls) == 1
    assert update.target_note_path == "people/priya-shah.md"
    assert compiled_truth in update.new_content
    # Old top-section prose is gone, replaced, not appended alongside.
    assert "First screen 2026-06-01" not in update.new_content
    # The original Timeline entries survive byte-for-byte -- compile is
    # additive-only re-synthesis of Compiled Truth, never a Timeline rewrite.
    assert "### 2026-06-01 — recruiter screen\n- Introductory call, discussed the VP Eng role." in (
        update.new_content
    )
    assert (
        "- **2026-06-01** | Referenced in [some-meeting](meetings/2026/2026-06-01-screen.md)"
        in update.new_content
    )

    written = apply_entity_compile(workspace, update)

    assert written is True
    on_disk = (workspace.root_path / "people/priya-shah.md").read_text()
    assert compiled_truth in on_disk


def test_prepare_entity_compile_unknown_slug_raises_without_model_call(workspace, kb_path):
    client = FakeClient([])

    with pytest.raises(IngestError, match="No entity note found for slug 'nonexistent-slug'"):
        prepare_entity_compile(workspace, client, "nonexistent-slug")

    assert client.calls == []


def test_apply_entity_compile_skips_stale_update_without_clobbering(workspace, kb_path):
    _write_person(kb_path, "priya-shah")
    client = FakeClient([{"compiled_truth": "New synthesized truth."}])
    update = prepare_entity_compile(workspace, client, "priya-shah")

    # The file changes on disk after prepare, before apply.
    target = workspace.root_path / "people/priya-shah.md"
    hand_edit = target.read_text() + "\n<!-- user's own concurrent edit -->\n"
    target.write_text(hand_edit)

    written = apply_entity_compile(workspace, update)

    assert written is False
    # Never clobbered -- the user's concurrent edit is exactly what's on disk.
    assert target.read_text() == hand_edit


# --------------------------------------------------------------------------
# Full resynthesis (docs/adr/0017, Stage 2): `wakil entities compile SLUG
# --full`.


class _PromptCapturingClient(FakeClient):
    """Same scripted-response behavior as FakeClient, but also records each
    call's cacheable_prefix -- the piece build_full_resynthesis_prompt
    puts the Timeline text in -- separately from the (system, prompt) tuple
    every other test in this file already asserts on, so adding this
    doesn't change client.calls' shape for those tests."""

    def __init__(self, payloads=None):
        super().__init__(payloads)
        self.cacheable_prefixes: list[str | None] = []

    def complete(self, system, prompt, max_tokens=8192, *, cacheable_prefix=None):
        self.cacheable_prefixes.append(cacheable_prefix)
        return super().complete(system, prompt, max_tokens, cacheable_prefix=cacheable_prefix)


def test_prepare_entity_full_resynthesis_happy_path_and_timeline_only_prompt(workspace, kb_path):
    _write_person(kb_path, "priya-shah")
    compiled_truth = (
        "**Recruiter** at [[companies/acme|Acme]]. Running a second search for the same "
        "VP Eng role after the first one fell through."
    )
    client = _PromptCapturingClient([{"compiled_truth": compiled_truth}])

    update = prepare_entity_full_resynthesis(workspace, client, "priya-shah")

    assert len(client.calls) == 1
    assert update.target_note_path == "people/priya-shah.md"
    assert compiled_truth in update.new_content
    # Old top-section prose is gone, replaced, not appended alongside.
    assert "First screen 2026-06-01" not in update.new_content
    # The original Timeline entries survive byte-for-byte -- full resynthesis
    # only ever rewrites Compiled Truth, never the Timeline itself.
    assert "### 2026-06-01 — recruiter screen\n- Introductory call, discussed the VP Eng role." in (
        update.new_content
    )
    assert (
        "- **2026-06-01** | Referenced in [some-meeting](meetings/2026/2026-06-01-screen.md)"
        in update.new_content
    )

    # ADR 0017: the full-resynthesis prompt is Timeline-only -- the note's
    # prior Compiled Truth text is never shown to the model, on purpose
    # (build_full_resynthesis_prompt takes no top_section parameter at all).
    # Verify that directly against what was actually sent, in both halves of
    # the call (the variable prompt suffix and the cacheable Timeline
    # prefix), not just that the function ran.
    system, prompt = client.calls[0]
    old_top_section_text = "**Recruiter** at [[companies/acme|Acme]]. First screen 2026-06-01."
    assert old_top_section_text not in prompt
    assert "First screen 2026-06-01" not in prompt
    prefix = client.cacheable_prefixes[0]
    assert prefix is not None
    assert old_top_section_text not in prefix
    assert "First screen 2026-06-01" not in prefix
    # The Timeline itself *is* in the cacheable prefix -- confirming the
    # prompt isn't simply empty of everything, only of the prior compiled
    # truth specifically.
    assert "### 2026-06-01 — recruiter screen" in prefix
    assert "Introductory call, discussed the VP Eng role." in prefix


def test_prepare_entity_full_resynthesis_rejects_empty_compiled_truth(workspace, kb_path):
    _write_person(kb_path, "priya-shah")
    client = FakeClient([{"compiled_truth": "   "}])

    with pytest.raises(ModelContractError, match="empty compiled_truth"):
        prepare_entity_full_resynthesis(workspace, client, "priya-shah")

    # _merge_entity_note's "empty means no change" fallback is correct for
    # additive mode but would silently keep the old Compiled Truth here,
    # while the CLI still reported success -- must raise instead of
    # returning a no-op EntityUpdate.


def test_validate_proposal_rejects_entity_update_with_invalid_frontmatter(workspace, kb_path):
    _write_person(kb_path, "priya-shah")
    proposal = EnrichmentProposal(source_id=1, title="t")
    proposal.entity_updates = [
        EntityUpdate(
            target_note_path="people/priya-shah.md",
            old_content=REAL_SHAPED_PERSON,
            new_content=REAL_SHAPED_PERSON.replace("status: active", "status: bogus-value"),
        )
    ]
    issues = validate_proposal(proposal)
    assert any("bogus-value" in str(issue) for issue in issues)


def test_candidate_entity_notes_finds_pages_relevance_search_would_bury(workspace, kb_path):
    # A sparse entity stub, indistinguishable from many other short notes by
    # relevance ranking, but exactly the page entity resolution must match.
    companies = kb_path / "companies"
    companies.mkdir()
    (companies / "mosaic-private-markets.md").write_text(
        "---\ntype: company\nname: Mosaic\n---\n\n# Mosaic\n\nPrivate markets software.\n"
    )
    with open_session(workspace) as session:
        workspace_id, _ = _require_workspace_ids(session, workspace)
        index_notes(session, workspace_id, workspace.root_path)
        session.commit()

        matches = _candidate_entity_notes(
            session,
            workspace_id,
            "Meeting is with Eleni Karahalios to discuss Mosaic and Jane Doe.",
            load_entity_schemas(),
        )

    paths = {path for path, _ in matches}
    assert "companies/mosaic-private-markets.md" in paths
    assert "people/jane-doe.md" in paths
    # Sentence-initial "Meeting" must not be treated as a proper noun.
    assert not any(p.startswith("meetings/") for p in paths)


def test_candidate_entity_notes_matches_filename_derived_title_terms(workspace, kb_path):
    # A company only named in the audio filename ("hovnanian-offer-sync"),
    # never spoken aloud in the transcript body itself — the humanized
    # title is lowercase, so _PROPER_NOUN_RE alone can never see it.
    companies = kb_path / "companies"
    companies.mkdir()
    (companies / "hovnanian-homes.md").write_text(
        "---\ntype: company\nname: K. Hovnanian Homes\naliases:\n- Hovnanian\n---\n\n"
        "# K. Hovnanian Homes\n"
    )
    with open_session(workspace) as session:
        workspace_id, _ = _require_workspace_ids(session, workspace)
        index_notes(session, workspace_id, workspace.root_path)
        session.commit()

        no_hint = _candidate_entity_notes(
            session, workspace_id, "We talked about the offer and the team.", load_entity_schemas()
        )
        with_hint = _candidate_entity_notes(
            session,
            workspace_id,
            "We talked about the offer and the team.",
            load_entity_schemas(),
            extra_terms=_title_terms("hovnanian kyle carnes offer sync"),
        )

    assert "companies/hovnanian-homes.md" not in {p for p, _ in no_hint}
    assert "companies/hovnanian-homes.md" in {p for p, _ in with_hint}


def test_title_terms_drops_generic_filename_words():
    terms = _title_terms("hovnanian kyle carnes offer sync")
    assert "hovnanian" in terms
    assert "kyle" in terms
    assert "carnes" in terms
    assert "offer" not in terms
    assert "sync" not in terms


def test_is_noise_candidate_drops_backchannel_words():
    # Discourse markers only look like proper nouns because they open a
    # sentence or a spoken backchannel — a standard stopword list catches
    # these more completely than a hand-grown set ever could.
    assert _is_noise_candidate("Mm-hmm")
    assert _is_noise_candidate("Right")
    assert _is_noise_candidate("Okay")


def test_is_noise_candidate_drops_common_single_token_words():
    # Tangential small talk ("we grabbed Indian food", "he ordered Steak")
    # picks up capitalized common nouns/adjectives that aren't discourse
    # markers and aren't in any stopword list — only a common-word
    # frequency filter catches these.
    assert _is_noise_candidate("Indian")
    assert _is_noise_candidate("Steak")
    # A short legitimate company name must survive the same filter.
    assert not _is_noise_candidate("Mosaic")


def test_is_noise_candidate_keeps_multiword_names():
    # The common-word filter only ever applies to single-token candidates —
    # a 2-4 word capitalized run is too strong a proper-noun signal to
    # second-guess by word frequency.
    assert not _is_noise_candidate("Ian Gutwinski")
    assert not _is_noise_candidate("Riviera Partners")


def test_is_noise_candidate_drops_attached_context_delimiter():
    # Defense-in-depth: if raw text ever contains the context-expansion
    # delimiter (wakil.app.context_references), "Attached Context" alone
    # regex-matches as a 2-word candidate.
    assert _is_noise_candidate("Attached Context")


def test_enrichment_related_notes_include_name_matched_entities(workspace, transcript, kb_path):
    companies = kb_path / "companies"
    companies.mkdir()
    (companies / "mosaic-private-markets.md").write_text(
        "---\ntype: company\nname: Mosaic\n---\n\n# Mosaic\n\nPrivate markets software.\n"
    )
    with open_session(workspace) as session:
        workspace_id, _ = _require_workspace_ids(session, workspace)
        index_notes(session, workspace_id, workspace.root_path)
        session.commit()

    source_id = _capture(workspace, transcript, context="Backchannel call about Mosaic.")
    proposal = prepare_enrichment(workspace, source_id, FakeClient())

    assert any(hit.ref == "companies/mosaic-private-markets.md" for hit in proposal.related_notes)


def test_enrichment_prioritizes_context_referenced_notes(workspace, transcript, kb_path):
    from wakil.app.context_references import resolve_context

    people = kb_path / "people"
    people.mkdir(exist_ok=True)
    (people / "referenced-person.md").write_text(
        "---\ntype: person\nname: Referenced Person\n---\n\n# Referenced Person\n\nBio.\n"
    )
    with open_session(workspace) as session:
        workspace_id, _ = _require_workspace_ids(session, workspace)
        index_notes(session, workspace_id, workspace.root_path)
        session.commit()

    resolved, _ = resolve_context(
        context=["Prep doc: @file:people/referenced-person.md"],
        context_files=[],
        workspace_root=workspace.root_path,
    )
    capture_proposal = prepare_capture(
        workspace,
        "transcript",
        _capture_client(),
        file=transcript,
        context=resolved.text,
        context_digest=resolved.digest,
        context_referenced_paths=resolved.referenced_paths,
    )
    source_id = apply_capture(workspace, capture_proposal).source_id

    # No --context repeated on enrich: the digest/referenced paths persisted
    # at capture time are read back from Source.metadata_json.
    proposal = prepare_enrichment(workspace, source_id, FakeClient())

    assert proposal.related_notes[0].ref == "people/referenced-person.md"
    assert proposal.related_notes[0].engine == "user-referenced"


def test_enrichment_related_search_uses_digest_not_raw_attachment_dump(
    workspace, transcript, kb_path
):
    from wakil.app.context_references import resolve_context

    # This company's name only appears inside the attached file's own
    # content, never in the digest (which excludes attachment blocks) or the
    # transcript -- it must not surface as a name-matched candidate.
    companies = kb_path / "companies"
    companies.mkdir()
    (companies / "buried-co.md").write_text(
        "---\ntype: company\nname: Buried Co\n---\n\n# Buried Co\n"
    )
    attachment = kb_path / "attachment.md"
    attachment.write_text("Buried Co is mentioned only in here.")
    with open_session(workspace) as session:
        workspace_id, _ = _require_workspace_ids(session, workspace)
        index_notes(session, workspace_id, workspace.root_path)
        session.commit()

    resolved, _ = resolve_context(
        context=["Prep notes @file:attachment.md"],
        context_files=[],
        workspace_root=workspace.root_path,
    )
    capture_proposal = prepare_capture(
        workspace,
        "transcript",
        _capture_client(),
        file=transcript,
        context=resolved.text,
        context_digest=resolved.digest,
        context_referenced_paths=resolved.referenced_paths,
    )
    source_id = apply_capture(workspace, capture_proposal).source_id

    proposal = prepare_enrichment(workspace, source_id, FakeClient())

    assert not any(hit.ref == "companies/buried-co.md" for hit in proposal.related_notes)
    assert any(hit.ref == "attachment.md" for hit in proposal.related_notes)


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
    proposal = prepare_enrichment(
        workspace, source_id, FakeClient([payload, RESOLUTION_JSON, REVISION_JSON])
    )
    assert proposal.proposed_note.path.startswith("drafts/")


def test_sanitize_note_leaves_well_formed_dated_path_unchanged(workspace, transcript):
    # Regression: a meeting-type primary note legitimately keeps a leading
    # date prefix the H1 doesn't carry (e.g. "2026-07-09-claims-kickoff.md"
    # / "# Claims Kickoff") -- that's not slug drift and must not be "fixed."
    source_id = _capture(workspace, transcript)
    proposal = prepare_enrichment(
        workspace, source_id, FakeClient([MODEL_JSON, RESOLUTION_JSON, REVISION_JSON])
    )
    assert proposal.proposed_note.path == "meetings/2026/2026-07-09-claims-kickoff.md"
    assert proposal.warnings == []
    assert validate_proposal(proposal) == []


def test_sanitize_note_corrects_unslugified_filename_and_self_link(workspace, transcript):
    source_id = _capture(workspace, transcript)
    payload = dict(
        MODEL_JSON,
        proposed_note={
            "path": "concepts/Guns Germs.md",
            "markdown": (
                "---\ntype: concept\nname: Guns Germs\n"
                "created: 2026-07-09\nupdated: 2026-07-09\n---\n\n"
                "# Guns Germs\n\nSee also [[concepts/Guns Germs.md]] for background.\n"
            ),
        },
    )
    proposal = prepare_enrichment(
        workspace, source_id, FakeClient([payload, RESOLUTION_JSON, REVISION_JSON])
    )

    assert proposal.proposed_note.path == "concepts/guns-germs.md"
    # The H1 and `name:` frontmatter stay exactly as authored -- only the
    # filename (and a self-link that pointed at the old, unslugged filename)
    # are corrected.
    assert "# Guns Germs" in proposal.proposed_note.content
    assert "name: Guns Germs" in proposal.proposed_note.content
    assert "[[concepts/guns-germs.md]]" in proposal.proposed_note.content
    assert "Guns Germs.md]]" not in proposal.proposed_note.content
    assert any("Corrected the proposed note's filename" in w for w in proposal.warnings)
    assert validate_proposal(proposal) == []


def test_validate_proposal_rejects_proposed_note_outside_its_type_schema_directory(workspace):
    # `_build_stub_entities` always routes a new page under its type's own
    # schema.directory; the model-chosen primary note path gets no such
    # guarantee, so this is a hard stop rather than a best-guess move.
    proposal = EnrichmentProposal(source_id=1, title="t")
    proposal.proposed_note = ProposedFile(
        path="concepts/misplaced.md",
        content=(
            "---\ntype: person\nname: Misplaced Person\n"
            "created: 2026-07-09\n---\n\n# Misplaced Person\n"
        ),
    )
    issues = validate_proposal(proposal)
    assert any("belong under people/" in str(issue) for issue in issues)


def test_validate_proposal_allows_proposed_note_in_a_subdirectory_of_its_schema_directory(
    workspace,
):
    # "meetings/2026/..." is a subdirectory of "meetings", not a mismatch.
    proposal = EnrichmentProposal(source_id=1, title="t")
    proposal.proposed_note = ProposedFile(
        path="meetings/2026/2026-07-09-claims-kickoff.md",
        content=(
            "---\ntype: meeting\ntitle: Claims Kickoff\ndate: 2026-07-09\n"
            "created: 2026-07-09\n---\n\n# Claims Kickoff\n"
        ),
    )
    assert validate_proposal(proposal) == []


def test_extraction_retry_then_success(workspace, transcript):
    source_id = _capture(workspace, transcript)
    client = FakeClient(["not json at all", MODEL_JSON, RESOLUTION_JSON, REVISION_JSON])

    proposal = prepare_enrichment(workspace, source_id, client)

    # Call 2 is the retry: same extraction system prompt, error appended.
    assert len(client.calls) == 4
    assert client.calls[1][0] == client.calls[0][0]
    assert "was not valid" in client.calls[1][1]
    assert proposal.summary == MODEL_JSON["summary"]


def test_extraction_double_failure_is_visible(workspace, transcript):
    source_id = _capture(workspace, transcript)
    client = FakeClient(["not json", "still not json"])

    # Never silently coerced to an empty proposal — the failure surfaces.
    with pytest.raises(IngestError, match="extraction failed"):
        prepare_enrichment(workspace, source_id, client)
    assert len(client.calls) == 2


def test_resolution_double_failure_degrades_visibly(workspace, transcript):
    source_id = _capture(workspace, transcript)
    client = FakeClient([MODEL_JSON, "bad", "still bad"])

    proposal = prepare_enrichment(workspace, source_id, client)

    assert len(client.calls) == 3
    assert proposal.entity_resolutions == []
    assert proposal.stub_entities == []
    assert any("Entity resolution failed" in warning for warning in proposal.warnings)
    # Extraction results survive and remain applicable.
    assert proposal.summary == MODEL_JSON["summary"]
    apply_enrichment(workspace, proposal)


def test_build_stub_entities_warns_for_directory_less_type(workspace):
    # "index" is a real schema type (MOC/navigation pages) with
    # `directory: null` — it has nowhere to be routed, so the create is
    # skipped, but that skip must be visible in proposal.warnings rather
    # than silent (issue #40).
    proposal = EnrichmentProposal(
        source_id=1,
        title="Reading List",
        entity_resolutions=[
            EntityResolution(name="Reading List", entity_type="index", action="create"),
        ],
    )

    stubs = ingest_service._build_stub_entities(workspace, proposal)

    assert stubs == []
    assert any(
        "Reading List" in warning
        and "index" in warning
        and "no canonical directory" in warning
        for warning in proposal.warnings
    )


def test_build_stub_entities_routable_type_unaffected(workspace):
    # A normal, routable type must not gain a spurious warning as a side
    # effect of the directory-less-type fix.
    proposal = EnrichmentProposal(
        source_id=1,
        title="Some Source",
        entity_resolutions=[
            EntityResolution(name="Dana Prieto", entity_type="person", action="create"),
        ],
    )

    stubs = ingest_service._build_stub_entities(workspace, proposal)

    assert [stub.path for stub in stubs] == ["people/dana-prieto.md"]
    assert proposal.warnings == []


def test_build_stub_entities_carries_proposed_frontmatter_confidence(workspace):
    # A create-time frontmatter value inferred from thin evidence (issue #72,
    # the create-path counterpart of #39's EntityRevision.confidence) must be
    # distinguishable downstream, not merged in looking exactly as confident
    # as a well-supported one.
    proposal = EnrichmentProposal(
        source_id=1,
        title="Some Source",
        entity_resolutions=[
            EntityResolution(
                name="Dana Prieto",
                entity_type="person",
                action="create",
                proposed_frontmatter_confidence=0.2,
            ),
        ],
    )

    stubs = ingest_service._build_stub_entities(workspace, proposal)

    assert len(stubs) == 1
    assert stubs[0].confidence == 0.2


def test_build_stub_entities_confidence_defaults_to_none(workspace):
    proposal = EnrichmentProposal(
        source_id=1,
        title="Some Source",
        entity_resolutions=[
            EntityResolution(name="Dana Prieto", entity_type="person", action="create"),
        ],
    )

    stubs = ingest_service._build_stub_entities(workspace, proposal)

    assert len(stubs) == 1
    assert stubs[0].confidence is None


def test_stub_suppressed_for_source_self_mirror_create(workspace):
    # Issue #58: entity-resolution satisfying "create a minimal stub rather
    # than skip it" (issue #44) by proposing `entity_type: source` for the
    # very source being enriched is a structural no-op -- that source is
    # already captured as the raw file. The exact-name case (a bare-link
    # bookmark whose title becomes both the source's own title and the
    # resolution's proposed name).
    proposal = EnrichmentProposal(
        source_id=1,
        title="Building a Router Table",
        entity_resolutions=[
            EntityResolution(
                name="Building a Router Table", entity_type="source", action="create"
            ),
        ],
    )

    stubs = ingest_service._build_stub_entities(workspace, proposal)

    assert stubs == []
    assert any(
        "Building a Router Table" in warning
        and "source" in warning
        and "mirror" in warning
        for warning in proposal.warnings
    )
    # Kept in entity_resolutions (like the proposed_note-subject suppression)
    # rather than dropped -- `source` has a real schema and directory, so
    # validate_proposal's hard-stop loop never sees it as an error either way.
    assert [r.entity_type for r in proposal.entity_resolutions] == ["source"]


def test_stub_suppressed_for_source_self_mirror_create_with_decorated_name(workspace):
    # The redundant create's proposed name is often a decorated variant of
    # the source's own title ("<title> Highlights", "Notes on <title>"), not
    # an exact match -- the suppression must still catch it.
    proposal = EnrichmentProposal(
        source_id=1,
        title="Deep Work",
        entity_resolutions=[
            EntityResolution(
                name="Deep Work Highlights", entity_type="source", action="create"
            ),
        ],
    )

    stubs = ingest_service._build_stub_entities(workspace, proposal)

    assert stubs == []
    assert any("mirror" in warning for warning in proposal.warnings)


def test_stub_kept_for_source_create_of_a_different_cited_source(workspace):
    # Not a blanket rejection of type=source creates: a source citing some
    # other, distinctly-named source it references is out of scope for this
    # suppression and must still get its stub.
    proposal = EnrichmentProposal(
        source_id=1,
        title="Weeknotes: July",
        entity_resolutions=[
            EntityResolution(
                name="Atomic Habits", entity_type="source", action="create"
            ),
        ],
    )

    stubs = ingest_service._build_stub_entities(workspace, proposal)

    assert [stub.path for stub in stubs] == ["sources/atomic-habits.md"]
    assert proposal.warnings == []


def test_stub_kept_for_legitimate_domain_type_create_alongside_own_subject(workspace):
    # No regression: a create-resolution for the source's actual subject,
    # under a real (non-`source`) domain type, is unaffected.
    proposal = EnrichmentProposal(
        source_id=1,
        title="Building a Router Table",
        entity_resolutions=[
            EntityResolution(
                name="Building a Router Table", entity_type="project", action="create"
            ),
        ],
    )

    stubs = ingest_service._build_stub_entities(workspace, proposal)

    assert [stub.path for stub in stubs] == ["projects/building-a-router-table.md"]
    assert proposal.warnings == []


# --------------------------------------------------------------------------
# Issue #73: when a create-resolution matching proposed_note's own subject
# disagrees with proposed_note's own `type:`, entity-resolution's decision
# must correct proposed_note's frontmatter type and path, not just suppress
# the redundant stub.


def test_stub_match_with_different_type_corrects_proposed_note(workspace):
    proposal = EnrichmentProposal(
        source_id=1,
        title="Guns Germs",
        entity_resolutions=[
            EntityResolution(name="Guns Germs", entity_type="project", action="create"),
        ],
    )
    proposal.proposed_note = ProposedFile(
        path="concepts/guns-germs.md",
        content=(
            "---\ntype: concept\nname: Guns Germs\ncreated: 2026-07-09\n---\n\n"
            "# Guns Germs\n\nSynthesized body prose that must survive untouched.\n"
        ),
    )

    stubs = ingest_service._build_stub_entities(workspace, proposal)

    # No duplicate stub — same suppression as issue #36.
    assert stubs == []
    # But unlike #36's original fix, proposed_note itself is now corrected
    # to entity-resolution's (better-informed) type and path.
    assert proposal.proposed_note.path == "projects/guns-germs.md"
    metadata = frontmatter.loads(proposal.proposed_note.content).metadata
    assert metadata["type"] == "project"
    assert metadata["name"] == "Guns Germs"
    # The synthesized markdown body is preserved verbatim — only frontmatter
    # `type:` and the file path moved.
    assert "Synthesized body prose that must survive untouched." in proposal.proposed_note.content
    assert "# Guns Germs" in proposal.proposed_note.content
    assert any(
        "Corrected the proposed note's type from 'concept' to 'project'" in warning
        for warning in proposal.warnings
    )
    assert any(
        "Guns Germs" in warning and "already represented by the proposed note" in warning
        for warning in proposal.warnings
    )


def test_stub_match_with_same_type_leaves_proposed_note_unchanged(workspace):
    original_content = (
        "---\ntype: concept\nname: Guns Germs\ncreated: 2026-07-09\n---\n\n"
        "# Guns Germs\n\nSynthesized body prose.\n"
    )
    proposal = EnrichmentProposal(
        source_id=1,
        title="Guns Germs",
        entity_resolutions=[
            EntityResolution(name="Guns Germs", entity_type="concept", action="create"),
        ],
    )
    proposal.proposed_note = ProposedFile(path="concepts/guns-germs.md", content=original_content)

    stubs = ingest_service._build_stub_entities(workspace, proposal)

    assert stubs == []
    # Matches #36's existing behavior exactly: suppressed, but no spurious
    # correction since the types already agree.
    assert proposal.proposed_note.path == "concepts/guns-germs.md"
    assert proposal.proposed_note.content == original_content
    assert not any("Corrected the proposed note's type" in warning for warning in proposal.warnings)
    assert any(
        "Guns Germs" in warning and "already represented by the proposed note" in warning
        for warning in proposal.warnings
    )


def test_stub_for_unrelated_subject_leaves_proposed_note_unchanged(workspace):
    original_content = (
        "---\ntype: concept\nname: Guns Germs\ncreated: 2026-07-09\n---\n\n"
        "# Guns Germs\n\nSynthesized body prose.\n"
    )
    proposal = EnrichmentProposal(
        source_id=1,
        title="Guns Germs",
        entity_resolutions=[
            EntityResolution(name="Dana Prieto", entity_type="person", action="create"),
        ],
    )
    proposal.proposed_note = ProposedFile(path="concepts/guns-germs.md", content=original_content)

    stubs = ingest_service._build_stub_entities(workspace, proposal)

    # A genuinely different, unrelated subject still gets its own stub, and
    # proposed_note is completely untouched — no regression.
    assert [stub.path for stub in stubs] == ["people/dana-prieto.md"]
    assert proposal.proposed_note.path == "concepts/guns-germs.md"
    assert proposal.proposed_note.content == original_content
    assert not any("Corrected the proposed note's type" in warning for warning in proposal.warnings)


def test_index_source_create_does_not_block_apply(workspace, transcript):
    # Regression: entity-resolve/SKILL.md now steers the model toward
    # proposing entity_type: index, action: create for index/list-shaped
    # sources (issue #40). That resolution can't get a stub (directory:
    # null), and validate_proposal() hard-stops on any pending create whose
    # type has no canonical directory — so if _build_stub_entities left it
    # in proposal.entity_resolutions, the whole apply would be aborted
    # (nothing written, not even this source's own unrelated proposed_note)
    # every time this newly-encouraged shape came up. It must not be.
    source_id = _capture(workspace, transcript)
    index_resolution_json = {
        "entities": [
            {
                "name": "Reading List",
                "entity_type": "index",
                "action": "create",
                "confidence": 0.8,
            },
        ]
    }
    client = FakeClient([MODEL_JSON, index_resolution_json])

    proposal = prepare_enrichment(workspace, source_id, client)

    # No stub could be built and the resolution is gone from
    # entity_resolutions -- validate_proposal's create-scanning loop never
    # sees it, so it can no longer trigger a hard stop.
    assert proposal.stub_entities == []
    assert proposal.entity_resolutions == []
    assert any(
        "Reading List" in warning
        and "index" in warning
        and "no canonical directory" in warning
        for warning in proposal.warnings
    )
    assert validate_proposal(proposal) == []

    # The rest of the proposal (unrelated proposed_note) still applies.
    result = apply_enrichment(workspace, proposal)
    assert (workspace.root_path / "meetings/2026/2026-07-09-claims-kickoff.md").exists()
    assert result.files_written == ["meetings/2026/2026-07-09-claims-kickoff.md"]


def test_validate_proposal_still_hard_stops_on_unknown_type(workspace):
    # The index/no-directory case above must be neutralized narrowly -- a
    # create for a type with no schema at all is a genuinely different,
    # real error and must still hard-stop validate_proposal.
    proposal = EnrichmentProposal(
        source_id=1,
        title="Some Source",
        entity_resolutions=[
            EntityResolution(
                name="Mystery Thing", entity_type="not-a-real-type", action="create"
            ),
        ],
    )

    stubs = ingest_service._build_stub_entities(workspace, proposal)

    assert stubs == []
    # Still kept in entity_resolutions -- unlike the index/no-directory case.
    assert [r.entity_type for r in proposal.entity_resolutions] == ["not-a-real-type"]

    issues = validate_proposal(proposal)
    assert len(issues) == 1
    assert "no entity schema defines type" in issues[0].message


def test_prepare_enrichment_warns_when_nothing_produced(workspace, transcript):
    # Issue #44: no proposed_note, no stub (create was skipped), no entity
    # update -- a source that resolves to a complete no-op must say so
    # explicitly, naming the source, rather than staying silent.
    extraction = dict(MODEL_JSON, proposed_note=None)
    resolution = {
        "entities": [
            {"name": "Acme", "entity_type": "company", "action": "skip", "confidence": 0.4}
        ]
    }
    source_id = _capture(workspace, transcript)
    client = FakeClient([extraction, resolution])
    proposal = prepare_enrichment(workspace, source_id, client)

    assert proposal.proposed_note is None
    assert proposal.stub_entities == []
    assert proposal.entity_updates == []
    assert any(
        str(source_id) in warning and "nothing will be written" in warning
        for warning in proposal.warnings
    )


def test_prepare_enrichment_sets_source_captured_date_from_retrieved_at(workspace, transcript):
    # Wiring for issue #77: the source's own captured/retrieved date must be
    # threaded onto the proposal so a placeholder Timeline heading has a
    # real fallback available at write time.
    extraction = dict(MODEL_JSON, proposed_note=None)
    resolution = {"entities": []}
    source_id = _capture(workspace, transcript)
    client = FakeClient([extraction, resolution])
    proposal = prepare_enrichment(workspace, source_id, client)

    with open_session(workspace) as session:
        source = session.get(Source, source_id)
        expected = (source.retrieved_at or source.created_at).date().isoformat()

    assert proposal.source_captured_date == expected


def test_prepare_enrichment_no_false_positive_warning_when_note_produced(workspace, transcript):
    # Regression guard: a source that does produce a proposed note must not
    # also get the "nothing will be written" warning.
    source_id = _capture(workspace, transcript)
    client = FakeClient()  # default MODEL_JSON has a proposed_note
    proposal = prepare_enrichment(workspace, source_id, client)

    assert proposal.proposed_note is not None
    assert not any("nothing will be written" in warning for warning in proposal.warnings)


def test_prepare_enrichment_no_false_positive_warning_when_stub_produced(
    workspace, transcript
):
    # A create-only source (no proposed_note, but a stub entity) also must
    # not get the no-op warning.
    extraction = dict(MODEL_JSON, proposed_note=None)
    resolution = {
        "entities": [
            {
                "name": "Dana Prieto",
                "entity_type": "person",
                "action": "create",
                "confidence": 0.85,
                "proposed_frontmatter": {"status": "active", "role": "Claims platform lead"},
            }
        ]
    }
    source_id = _capture(workspace, transcript)
    client = FakeClient([extraction, resolution])
    proposal = prepare_enrichment(workspace, source_id, client)

    assert proposal.stub_entities != []
    assert not any("nothing will be written" in warning for warning in proposal.warnings)


def test_enrichment_guides_reach_prompt(workspace, transcript):
    (workspace.root_path / "RESOLVER.md").write_text("# Resolver\n\nMeetings go in meetings/.\n")
    source_id = _capture(workspace, transcript)
    client = FakeClient()
    prepare_enrichment(workspace, source_id, client)

    prompt = client.calls[0][1]
    assert "Workspace guidance from RESOLVER.md" in prompt
    # SCHEMA.md is no longer read at all — its page-shape/metadata role is
    # covered structurally by the entity-schema catalog instead.
    assert "SCHEMA.md" not in prompt
    # Frontmatter is stripped from the analyzed text.
    assert "meeting_date:" not in prompt
    # Routing guidance also reaches the resolution call.
    assert "Workspace guidance from RESOLVER.md" in client.calls[1][1]


# --------------------------------------------------------------------------
# Backfill: title/abstract for sources captured before docs/adr/0010


def test_plan_abstract_backfill_finds_sources_missing_abstract(workspace, transcript):
    source_id = _capture(workspace, transcript)
    # Simulate a pre-ADR-0010 source: metadata_json has no `abstract` key.
    with open_session(workspace) as session:
        source = session.get(Source, source_id)
        source.metadata_json = json.dumps({"meeting_date": "2026-07-09"})
        session.commit()

    items = plan_abstract_backfill(workspace, _capture_client())
    assert [item.source_id for item in items] == [source_id]
    assert items[0].title == CAPTURE_METADATA_JSON["title"]
    assert items[0].abstract == CAPTURE_METADATA_JSON["abstract"]


def test_plan_abstract_backfill_skips_sources_that_already_have_one(workspace, transcript):
    _capture(workspace, transcript)  # capture already writes an abstract
    assert plan_abstract_backfill(workspace, _capture_client()) == []


def test_apply_abstract_backfill_rewrites_frontmatter_and_source_row(workspace, transcript):
    source_id = _capture(workspace, transcript)
    with open_session(workspace) as session:
        source = session.get(Source, source_id)
        raw_path = source.raw_text_path
        source.metadata_json = json.dumps({"meeting_date": "2026-07-09"})
        source.title = "old filename title"
        session.commit()

    payload = {
        "title": "2026-07-20 Backfilled Title",
        "abstract": "A freshly backfilled abstract.",
    }
    items = plan_abstract_backfill(workspace, _capture_client(payload))
    updated = apply_abstract_backfill(workspace, items)

    assert updated == [raw_path]
    on_disk = (workspace.root_path / raw_path).read_text()
    assert "title: 2026-07-20 Backfilled Title" in on_disk
    assert "abstract: A freshly backfilled abstract." in on_disk
    # The rest of the raw file survives untouched.
    assert "meeting_date: '2026-07-09'" in on_disk
    assert "# 2026-07-09-raw-meeting" in on_disk

    with open_session(workspace) as session:
        source = session.get(Source, source_id)
        assert source.title == "2026-07-20 Backfilled Title"
        metadata = json.loads(source.metadata_json)
        assert metadata["title"] == "2026-07-20 Backfilled Title"
        assert metadata["abstract"] == "A freshly backfilled abstract."


def test_apply_abstract_backfill_never_touches_memories_or_status(workspace, transcript):
    source_id = _capture(workspace, transcript)
    with open_session(workspace) as session:
        source = session.get(Source, source_id)
        source.metadata_json = json.dumps({})
        session.commit()

    items = plan_abstract_backfill(workspace, _capture_client())
    apply_abstract_backfill(workspace, items)

    with open_session(workspace) as session:
        source = session.get(Source, source_id)
        assert source.status == "raw"  # unchanged -- backfill never enriches
        assert session.scalar(select(Memory)) is None


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
