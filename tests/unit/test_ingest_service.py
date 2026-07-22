import json
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select

from wakil.app.ingest_service import (
    EnrichmentProposal,
    EntityUpdate,
    IngestError,
    _candidate_entity_notes,
    _merge_entity_note,
    _require_workspace_ids,
    _title_terms,
    apply_abstract_backfill,
    apply_capture,
    apply_enrichment,
    clean_transcript,
    infer_meeting_date,
    parse_whisper_transcript,
    plan_abstract_backfill,
    prepare_capture,
    prepare_enrichment,
    slugify,
    strip_srt,
    transcript_frontmatter_template,
    validate_proposal,
)
from wakil.app.workspace_service import index_notes, init_workspace, open_session
from wakil.config.settings import WorkspaceConfig
from wakil.llm.schemas import EntityRevision
from wakil.schema.loader import load_entity_schemas
from wakil.storage.schema import IngestRun, Memory, Note, Relationship, Source

MODEL_JSON = {
    "title": "Claims Kickoff Meeting",
    "summary": "The team agreed to prototype FNOL routing using graph memory.",
    "key_points": ["Prototype approved", "Jane owns the routing design"],
    "memories": [
        {"type": "decision", "content": "Team will prototype FNOL routing.", "confidence": 0.9},
        {"type": "fact", "content": "Jane Doe owns the routing design.", "confidence": 0.8},
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

    def complete(self, system, prompt, max_tokens=8192):
        self.calls.append((system, prompt))
        assert self.queue, "FakeClient ran out of scripted responses"
        return self.queue.pop(0)


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


def _write_person(kb_path: Path, slug: str, content: str = REAL_SHAPED_PERSON) -> None:
    people = kb_path / "people"
    people.mkdir(exist_ok=True)
    (people / f"{slug}.md").write_text(content.replace("priya-shah", slug))


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
