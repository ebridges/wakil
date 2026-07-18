"""Entity-resolution node: create/update/skip decisions against fixture
notes, stub construction, and validate_proposal's hard stops
(docs/ingestion-refactor-spec.md testing strategy)."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wakil.app.ingest_service import (
    IngestError,
    apply_capture,
    apply_enrichment,
    prepare_capture,
    prepare_enrichment,
    validate_proposal,
)
from wakil.app.workspace_service import init_workspace
from wakil.config.settings import WorkspaceConfig
from wakil.schema.validate import validate_frontmatter

EXTRACTION = {
    "title": "Planning Sync",
    "summary": "A sync about the claims routing plan.",
    "key_points": [],
    "memories": [{"type": "fact", "content": "Routing plan reviewed.", "confidence": 0.8}],
    "relationships": [],
    "proposed_note": None,
}


class FakeClient:
    model = "fake-model"

    def __init__(self, payloads):
        self.queue = [json.dumps(p) if isinstance(p, dict) else p for p in payloads]

    def complete(self, system, prompt, max_tokens=8192):
        assert self.queue, "FakeClient ran out of scripted responses"
        return self.queue.pop(0)


@pytest.fixture
def workspace(kb_path: Path) -> WorkspaceConfig:
    init_workspace(kb_path)
    return WorkspaceConfig.load(kb_path)


def _enrich(workspace, kb_path, entities, extraction=None, revisions=None):
    transcript = kb_path / "sync.txt"
    transcript.write_text("We reviewed the routing plan.\n")
    source_id = apply_capture(
        workspace, prepare_capture(workspace, "transcript", file=transcript)
    ).source_id
    client = FakeClient(
        [extraction or EXTRACTION, {"entities": entities}, revisions or {"revisions": []}]
    )
    return prepare_enrichment(workspace, source_id, client)


def test_create_builds_schema_valid_stub(workspace, kb_path):
    proposal = _enrich(
        workspace,
        kb_path,
        [
            {
                "name": "Dana Prieto",
                "entity_type": "person",
                "action": "create",
                "confidence": 0.9,
                "proposed_frontmatter": {"status": "active", "role": "Platform lead"},
            }
        ],
    )

    assert [stub.path for stub in proposal.stub_entities] == ["people/dana-prieto.md"]
    stub = proposal.stub_entities[0]
    # Frontmatter is completed deterministically and satisfies the schema.
    assert "type: person" in stub.content
    assert "name: Dana Prieto" in stub.content
    assert "role: Platform lead" in stub.content
    today = datetime.now(UTC).date().isoformat()
    assert f"created: '{today}'" in stub.content or f"created: {today}" in stub.content
    # Compiled Truth / Timeline skeleton per docs/entity-model.md.
    assert "## Compiled Truth" in stub.content
    assert "## Timeline / Log" in stub.content
    assert validate_proposal(proposal) == []

    result = apply_enrichment(workspace, proposal)
    assert "people/dana-prieto.md" in result.files_written
    assert (kb_path / "people/dana-prieto.md").exists()


def test_update_and_skip_build_no_stub(workspace, kb_path):
    proposal = _enrich(
        workspace,
        kb_path,
        [
            {
                "name": "Jane Doe",
                "entity_type": "person",
                "action": "update",
                "target_note_path": "people/jane-doe.md",
                "proposed_frontmatter": {"role": "Routing owner"},
            },
            {"name": "Acme", "entity_type": "company", "action": "skip"},
        ],
    )

    assert proposal.stub_entities == []
    assert len(proposal.entity_resolutions) == 2
    assert validate_proposal(proposal) == []
    # Applying writes no files; the decisions remain visible in the preview.
    result = apply_enrichment(workspace, proposal)
    assert result.files_written == []


def test_create_for_existing_page_is_downgraded(workspace, kb_path):
    proposal = _enrich(
        workspace,
        kb_path,
        [
            {
                "name": "Jane Doe",
                "entity_type": "person",
                "action": "create",
                "proposed_frontmatter": {"status": "active"},
            }
        ],
    )

    assert proposal.stub_entities == []
    assert any("already exists" in warning for warning in proposal.warnings)
    assert validate_proposal(proposal) == []


def test_duplicate_creates_yield_one_stub(workspace, kb_path):
    entity = {
        "name": "Dana Prieto",
        "entity_type": "person",
        "action": "create",
        "proposed_frontmatter": {"status": "active"},
    }
    proposal = _enrich(workspace, kb_path, [entity, dict(entity)])
    assert [stub.path for stub in proposal.stub_entities] == ["people/dana-prieto.md"]


def test_unknown_entity_type_is_a_hard_stop(workspace, kb_path):
    proposal = _enrich(
        workspace,
        kb_path,
        [{"name": "The Guild", "entity_type": "guild", "action": "create"}],
    )

    assert proposal.stub_entities == []  # no best-guess write
    issues = validate_proposal(proposal)
    assert len(issues) == 1
    assert "no entity schema defines type 'guild'" in issues[0].message
    with pytest.raises(IngestError, match="failed validation"):
        apply_enrichment(workspace, proposal)


def test_type_without_directory_is_a_hard_stop(workspace, kb_path):
    proposal = _enrich(
        workspace,
        kb_path,
        [{"name": "Vault Meta", "entity_type": "meta", "action": "create"}],
    )

    assert proposal.stub_entities == []
    issues = validate_proposal(proposal)
    assert len(issues) == 1
    assert "no canonical directory" in issues[0].message


def test_proposed_note_frontmatter_is_validated(workspace, kb_path):
    extraction = dict(
        EXTRACTION,
        proposed_note={
            "path": "meetings/2026/sync.md",
            # meeting requires title, date, created — all missing here.
            "markdown": "---\ntype: meeting\n---\n\n# Sync\n",
        },
    )
    proposal = _enrich(workspace, kb_path, [], extraction=extraction)

    issues = validate_proposal(proposal)
    messages = {issue.message for issue in issues}
    assert any("title" in m and "missing" in m for m in messages)
    assert any("date" in m and "missing" in m for m in messages)
    with pytest.raises(IngestError, match="failed validation"):
        apply_enrichment(workspace, proposal)
    assert not (kb_path / "meetings/2026/sync.md").exists()


def test_proposed_note_without_type_is_blocked(workspace, kb_path):
    extraction = dict(
        EXTRACTION,
        proposed_note={"path": "drafts/loose.md", "markdown": "# Loose thoughts\n"},
    )
    proposal = _enrich(workspace, kb_path, [], extraction=extraction)

    issues = validate_proposal(proposal)
    assert len(issues) == 1
    assert "no `type:` frontmatter" in issues[0].message


def test_stub_frontmatter_passes_schema_validation(workspace, kb_path):
    proposal = _enrich(
        workspace,
        kb_path,
        [
            {
                "name": "Initech",
                "entity_type": "company",
                "action": "create",
                "proposed_frontmatter": {"category": "vendor"},
            }
        ],
    )
    stub = proposal.stub_entities[0]
    assert stub.path == "companies/initech.md"
    import frontmatter as frontmatter_lib

    metadata = frontmatter_lib.loads(stub.content).metadata
    assert validate_frontmatter("company", metadata) == []
