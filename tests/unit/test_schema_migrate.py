"""Phase D-cheap tests: dry-run diff generation and apply for each
cheap-tier fix type (docs/ingestion-refactor-spec.md testing strategy)."""

from pathlib import Path

import pytest

from wakil.app.schema_migrate_service import (
    MigrateError,
    apply_migrations,
    plan_schema_migration,
)
from wakil.app.workspace_service import init_workspace
from wakil.config.settings import WorkspaceConfig


def _write(kb: Path, rel: str, content: str) -> None:
    path = kb / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.fixture
def workspace(kb_path: Path) -> WorkspaceConfig:
    init_workspace(kb_path)
    return WorkspaceConfig.load(kb_path)


def _replan(config: WorkspaceConfig):
    init_workspace(config.root_path)  # re-index new files
    return plan_schema_migration(config)


def test_pristine_fixture_kb_needs_no_migration(workspace):
    assert plan_schema_migration(workspace).total_files == 0


def test_field_rename_underscore_to_dash(workspace, kb_path):
    _write(
        kb_path,
        "people/old-colleague.md",
        "---\ntype: person\nname: Old Colleague\nstatus: former\n"
        "end_date: 2025-01-31\ncreated: 2024-01-01\nupdated: 2025-01-31\n---\n\n# Old Colleague\n",
    )
    plan = _replan(workspace)

    proposals = plan.by_type["person"]
    assert len(proposals) == 1
    assert proposals[0].fixes == ["rename `end_date` to `end-date`"]
    assert "end-date: 2025-01-31" in proposals[0].new_content
    assert "end_date" not in proposals[0].new_content
    diff = proposals[0].diff()
    assert "-end_date: 2025-01-31" in diff
    assert "+end-date: 2025-01-31" in diff
    # Field order is preserved by the rewrite.
    assert proposals[0].new_content.index("status:") < proposals[0].new_content.index("end-date:")


def test_duplicate_field_dropped_when_equal_skipped_when_not(workspace, kb_path):
    _write(
        kb_path,
        "sources/articles/dup-equal.md",
        "---\ntype: source\ntitle: Dup Equal\norigin: article\ncaptured: 2026-01-01\n"
        "url: https://x.test/a\nlink: https://x.test/a\n---\nbody\n",
    )
    _write(
        kb_path,
        "sources/articles/dup-differs.md",
        "---\ntype: source\ntitle: Dup Differs\norigin: article\ncaptured: 2026-01-01\n"
        "url: https://x.test/a\nlink: https://x.test/b\n---\nbody\n",
    )
    plan = _replan(workspace)

    proposals = {p.path: p for p in plan.by_type["source"]}
    equal = proposals["sources/articles/dup-equal.md"]
    assert equal.fixes == ["drop `link` (exact duplicate of `url`)"]
    assert "link:" not in equal.new_content
    # The conflicting file is surfaced, never silently resolved.
    assert "sources/articles/dup-differs.md" not in proposals
    assert any("dup-differs" in note and "manual review" in note for note in plan.skipped)


def test_organization_retype(workspace, kb_path):
    _write(
        kb_path,
        "organization/ic5-ladder.md",
        "---\ntype: concept\nname: IC5 Ladder\ncreated: 2026-01-01\nupdated: 2026-01-01\n---\n"
        "\n# IC5 Ladder\n",
    )
    _write(
        kb_path,
        "concepts/real-concept.md",
        "---\ntype: concept\nname: Real Concept\ncreated: 2026-01-01\nupdated: 2026-01-01\n---\n"
        "\n# Real Concept\n",
    )
    plan = _replan(workspace)

    org = plan.by_type["organization"]
    assert [p.path for p in org] == ["organization/ic5-ladder.md"]
    assert "type: organization" in org[0].new_content
    assert any("retype organization/" in fix for fix in org[0].fixes)
    # A concept outside organization/ is untouched.
    assert "concept" not in plan.by_type


def test_title_caser_artifact_aligned(workspace, kb_path):
    _write(
        kb_path,
        "projects/1nsp/renovation.md",
        "---\ntype: project\nname: 1NSP Second Floor Amenity Renovation\n"
        "title: 1nsp Second Floor Amenity Renovation\n"
        "created: 2026-01-01\nupdated: 2026-01-01\n---\nbody\n",
    )
    _write(
        kb_path,
        "projects/personal/ml-course.md",
        "---\ntype: project\nname: README\ntitle: 'Chapter 0: Readme'\n"
        "created: 2026-01-01\nupdated: 2026-01-01\n---\nbody\n",
    )
    plan = _replan(workspace)

    proposals = {p.path: p for p in plan.by_type["project"]}
    fixed = proposals["projects/1nsp/renovation.md"]
    assert fixed.fixes == ["align mechanically re-cased `title` with authored `name`"]
    assert "title: 1NSP Second Floor Amenity Renovation" in fixed.new_content
    # A genuinely authored title is never touched.
    assert "projects/personal/ml-course.md" not in proposals


def test_quoted_type_normalized(workspace, kb_path):
    _write(
        kb_path,
        "sources/clippings/quoted.md",
        '---\ntype: "source"\ntitle: Quoted\norigin: manual\ncaptured: 2026-01-01\n---\nbody\n',
    )
    plan = _replan(workspace)

    proposals = plan.by_type["source"]
    assert [p.path for p in proposals] == ["sources/clippings/quoted.md"]
    assert proposals[0].fixes == ["normalize quoted `type:` value"]
    assert "type: source\n" in proposals[0].new_content


def test_unknown_types_and_plain_files_are_out_of_scope(workspace, kb_path):
    _write(kb_path, "drafts/odd.md", "---\ntype: daily-note\ntitle: Odd\n---\nbody\n")
    _write(kb_path, "drafts/notype.md", "---\ntags: [x]\n---\nbody\n")
    plan = _replan(workspace)
    assert plan.total_files == 0


def test_type_filter(workspace, kb_path):
    _write(
        kb_path,
        "people/a.md",
        "---\ntype: person\nname: A\nstatus: active\nend_date: 2025-01-01\n"
        "created: 2024-01-01\nupdated: 2024-01-01\n---\nbody\n",
    )
    _write(
        kb_path,
        "sources/clippings/b.md",
        '---\ntype: "source"\ntitle: B\norigin: manual\ncaptured: 2026-01-01\n---\nbody\n',
    )
    init_workspace(kb_path)

    plan = plan_schema_migration(workspace, entity_type="person")
    assert set(plan.by_type) == {"person"}

    with pytest.raises(MigrateError, match="No entity schema"):
        plan_schema_migration(workspace, entity_type="daily-note")


def test_apply_rewrites_and_preserves_body(workspace, kb_path):
    body = "\n# Old Colleague\n\nSome notes with trailing spaces  \nand a last line without newline"
    _write(
        kb_path,
        "people/old-colleague.md",
        "---\ntype: person\nname: Old Colleague\nstatus: former\nend_date: 2025-01-31\n"
        f"created: 2024-01-01\nupdated: 2025-01-31\n---\n{body}",
    )
    plan = _replan(workspace)
    proposals = plan.by_type["person"]

    written, stale = apply_migrations(workspace, proposals)
    assert written == ["people/old-colleague.md"]
    assert stale == []
    rewritten = (kb_path / "people/old-colleague.md").read_text()
    assert "end-date: 2025-01-31" in rewritten
    assert rewritten.endswith(body)  # body byte-identical

    # Idempotent: a fresh plan finds nothing left to fix.
    assert _replan(workspace).total_files == 0


def test_apply_skips_files_changed_since_planning(workspace, kb_path):
    _write(
        kb_path,
        "people/racy.md",
        "---\ntype: person\nname: Racy\nstatus: active\nend_date: 2026-01-01\n"
        "created: 2026-01-01\nupdated: 2026-01-01\n---\nbody\n",
    )
    plan = _replan(workspace)
    proposals = plan.by_type["person"]

    (kb_path / "people/racy.md").write_text("# user replaced the file entirely\n")
    written, stale = apply_migrations(workspace, proposals)
    assert written == []
    assert len(stale) == 1 and "changed on disk" in stale[0]
    assert (kb_path / "people/racy.md").read_text() == "# user replaced the file entirely\n"
