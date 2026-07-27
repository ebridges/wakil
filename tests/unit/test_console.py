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
