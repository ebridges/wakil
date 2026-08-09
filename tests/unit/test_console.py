"""Rich console output: dynamic content must not corrupt through markup parsing.

`console.print(f"...{dynamic}...")` interprets `[...]` in `dynamic` as Rich
markup tags. A wikilink like `[[companies/foo|Bar]]` gets read as nested
tags and silently mangled (reproduced directly against Rich: it renders as
`[]`) rather than raising — the kind of corruption that's easy to miss
because nothing errors, the text just quietly comes out wrong.
"""

from wakil.app.ingest_service import EnrichmentProposal, EntityUpdate, ProposedFile
from wakil.llm.schemas import EntityResolution
from wakil.ui.console import console, print_enrichment_proposal


def test_warning_containing_wikilinks_is_not_mangled_by_markup(capsys):
    proposal = EnrichmentProposal(
        source_id=1,
        title="Test",
        warnings=[
            "Corrected 1 entity link in the proposed note to match "
            "entity-resolution's own answer: "
            "[[companies/mosaic-app|Mosaic]] -> "
            "[[companies/mosaic-private-markets|Mosaic]]"
        ],
    )
    console.width = 200  # avoid line-wrapping splitting the assertion text
    print_enrichment_proposal(proposal)
    out = capsys.readouterr().out
    assert "[[companies/mosaic-app|Mosaic]]" in out
    assert "[[companies/mosaic-private-markets|Mosaic]]" in out
    assert "warning: []" not in out


def test_missing_update_target_name_survives_markup(capsys):
    """The name is the one thing this message exists to convey, and it comes
    from model output — a bracketed one was read as a style tag and vanished."""
    from wakil.app.ingest_service import _MissingUpdateTarget
    from wakil.ui.console import print_missing_update_targets

    console.width = 200
    print_missing_update_targets(
        [
            _MissingUpdateTarget(
                name="Q3 [draft] Planning",
                path="concepts/q3-planning.md",
                branches=["ingest/2026-08-01-notes"],
            )
        ],
        source_id=7,
    )
    out = capsys.readouterr().out
    assert "Q3 [draft] Planning" in out
    assert "concepts/q3-planning.md" in out
    assert "ingest/2026-08-01-notes" in out
    assert "wakil enrich 7 --force" in out


def test_entity_resolution_table_shows_relevance_column(capsys):
    proposal = EnrichmentProposal(
        source_id=1,
        title="Test",
        entity_resolutions=[
            EntityResolution(
                name="Lawrence Krubner",
                entity_type="person",
                action="update",
                target_note_path="people/lawrence-krubner.md",
                confidence=0.99,
                relevance="central",
            ),
            EntityResolution(
                name="Mosaic",
                entity_type="company",
                action="update",
                target_note_path="companies/mosaic-private-markets.md",
                confidence=0.95,
                relevance="peripheral",
            ),
        ],
    )
    console.width = 200
    print_enrichment_proposal(proposal)
    out = capsys.readouterr().out
    assert "Relevance" in out
    assert "central" in out
    assert "peripheral" in out


def test_low_confidence_entity_update_is_flagged_for_review(capsys):
    proposal = EnrichmentProposal(
        source_id=1,
        title="Test",
        entity_updates=[
            EntityUpdate(
                target_note_path="books/some-book.md",
                old_content="old",
                new_content="new",
                confidence=0.2,
            )
        ],
    )
    console.width = 200
    print_enrichment_proposal(proposal)
    out = capsys.readouterr().out
    assert "LOW-CONFIDENCE" in out
    assert "Flagged for review" in out
    assert "books/some-book.md" in out


def test_high_confidence_entity_update_is_not_flagged(capsys):
    proposal = EnrichmentProposal(
        source_id=1,
        title="Test",
        entity_updates=[
            EntityUpdate(
                target_note_path="books/some-book.md",
                old_content="old",
                new_content="new",
                confidence=0.9,
            )
        ],
    )
    console.width = 200
    print_enrichment_proposal(proposal)
    out = capsys.readouterr().out
    assert "LOW-CONFIDENCE" not in out
    assert "Flagged for review" not in out
    assert "confidence 0.90" in out


def test_low_confidence_new_entity_page_is_flagged_for_review(capsys):
    # Create-path counterpart of #39: a stub page whose proposed_frontmatter
    # was inferred from thin evidence (issue #72) must be distinguishable in
    # the preview, not rendered identically to a well-supported create.
    proposal = EnrichmentProposal(
        source_id=1,
        title="Test",
        stub_entities=[
            ProposedFile(
                path="books/some-book.md",
                content="---\ntype: book\n---\n",
                confidence=0.2,
            )
        ],
    )
    console.width = 200
    print_enrichment_proposal(proposal)
    out = capsys.readouterr().out
    assert "LOW-CONFIDENCE" in out
    assert "Flagged for review" in out
    assert "books/some-book.md" in out


def test_high_confidence_new_entity_page_is_not_flagged(capsys):
    proposal = EnrichmentProposal(
        source_id=1,
        title="Test",
        stub_entities=[
            ProposedFile(
                path="books/some-book.md",
                content="---\ntype: book\n---\n",
                confidence=0.9,
            )
        ],
    )
    console.width = 200
    print_enrichment_proposal(proposal)
    out = capsys.readouterr().out
    assert "LOW-CONFIDENCE" not in out
    assert "Flagged for review" not in out
    assert "confidence 0.90" in out


def test_low_confidence_proposed_note_is_flagged_for_review(capsys):
    # Fresh-primary-entity counterpart of #72/#39: extraction's own
    # proposed_note (a brand-new book/article page, not a stub built from
    # entity-resolution) must be flagged the same way when its frontmatter
    # was inferred from thin evidence (issue #93).
    proposal = EnrichmentProposal(
        source_id=1,
        title="Test",
        proposed_note=ProposedFile(
            path="books/some-book.md",
            content="---\ntype: book\n---\n",
            confidence=0.2,
        ),
    )
    console.width = 200
    print_enrichment_proposal(proposal)
    out = capsys.readouterr().out
    assert "LOW-CONFIDENCE" in out
    assert "Flagged for review" in out
    assert "books/some-book.md" in out


def test_high_confidence_proposed_note_is_not_flagged(capsys):
    proposal = EnrichmentProposal(
        source_id=1,
        title="Test",
        proposed_note=ProposedFile(
            path="books/some-book.md",
            content="---\ntype: book\n---\n",
            confidence=0.9,
        ),
    )
    console.width = 200
    print_enrichment_proposal(proposal)
    out = capsys.readouterr().out
    assert "LOW-CONFIDENCE" not in out
    assert "Flagged for review" not in out
    assert "confidence 0.90" in out


def test_capture_preview_warns_when_the_path_is_owned_but_the_file_is_gone(capsys):
    """apply refuses on the Source row, so a preview that only warns when the
    file is present leaves a `--yes` run aborting with no stated reason."""
    from wakil.app.ingest_service import CaptureProposal, ProposedFile
    from wakil.ui.console import print_capture_proposal

    console.width = 200
    print_capture_proposal(
        CaptureProposal(
            source_type="transcript",
            origin="meeting.txt",
            title="Second Take",
            text="x",
            content_hash="abc",
            raw_file=ProposedFile(path="sources/transcripts/2026-08-04-call.md", content="x"),
            collision=None,
            collision_source_id=3,
        )
    )
    out = capsys.readouterr().out.replace("\n", "")
    assert "raw capture of source" in out
    assert "#3" in out
