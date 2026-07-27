"""Unit tests for `wakil.app.schema_validate_service` — the reusable helpers
behind `wakil schema validate`."""

from pathlib import Path

from wakil.app.schema_validate_service import collect_markdown_files, validate_file

VALID = (
    "---\ntype: person\nname: Valid Colleague\nstatus: active\n"
    "created: 2024-01-01\nupdated: 2025-01-31\n---\n\n# Valid Colleague\n"
)

BAD_ENUM = (
    "---\ntype: person\nname: Bad Status\nstatus: not-a-real-status\n"
    "created: 2024-01-01\nupdated: 2025-01-31\n---\n\n# Bad Status\n"
)

MISSING_REQUIRED = (
    "---\ntype: person\nname: No Status\n"
    "created: 2024-01-01\nupdated: 2025-01-31\n---\n\n# No Status\n"
)

NO_TYPE = "---\nname: Untyped\n---\n\n# Untyped\n"

UNKNOWN_TYPE = "---\ntype: not-a-real-type\nname: Whoever\n---\n\n# Whoever\n"


def test_validate_file_ok_for_conformant_frontmatter(tmp_path: Path):
    path = tmp_path / "valid.md"
    path.write_text(VALID)

    result = validate_file(path, kb_root=None)

    assert result.ok
    assert result.errors == []
    assert result.load_error is None


def test_validate_file_reports_bad_enum(tmp_path: Path):
    path = tmp_path / "bad.md"
    path.write_text(BAD_ENUM)

    result = validate_file(path, kb_root=None)

    assert not result.ok
    assert any("status" in str(err) for err in result.errors)


def test_validate_file_reports_missing_required_field(tmp_path: Path):
    path = tmp_path / "missing.md"
    path.write_text(MISSING_REQUIRED)

    result = validate_file(path, kb_root=None)

    assert not result.ok
    assert any("required field is missing" in str(err) for err in result.errors)


def test_validate_file_flags_missing_type_as_load_error(tmp_path: Path):
    path = tmp_path / "untyped.md"
    path.write_text(NO_TYPE)

    result = validate_file(path, kb_root=None)

    assert not result.ok
    assert result.load_error is not None
    assert "type" in result.load_error


def test_validate_file_flags_unknown_type(tmp_path: Path):
    path = tmp_path / "unknown.md"
    path.write_text(UNKNOWN_TYPE)

    result = validate_file(path, kb_root=None)

    assert not result.ok
    assert any("no entity schema defines type" in str(err) for err in result.errors)


def test_validate_file_reports_unreadable_path(tmp_path: Path):
    missing = tmp_path / "does-not-exist.md"

    result = validate_file(missing, kb_root=None)

    assert not result.ok
    assert result.load_error is not None


def test_collect_markdown_files_from_directory(tmp_path: Path):
    (tmp_path / "a.md").write_text(VALID)
    (tmp_path / "b.md").write_text(VALID)
    (tmp_path / "c.txt").write_text("not markdown")

    files = collect_markdown_files([str(tmp_path)])

    assert files == sorted([tmp_path / "a.md", tmp_path / "b.md"])


def test_collect_markdown_files_from_explicit_file(tmp_path: Path):
    target = tmp_path / "one.md"
    target.write_text(VALID)

    files = collect_markdown_files([str(target)])

    assert files == [target]


def test_collect_markdown_files_from_glob(tmp_path: Path):
    (tmp_path / "x.md").write_text(VALID)
    (tmp_path / "y.md").write_text(VALID)

    files = collect_markdown_files([str(tmp_path / "*.md")])

    assert files == sorted([tmp_path / "x.md", tmp_path / "y.md"])


def test_collect_markdown_files_deduplicates_and_sorts(tmp_path: Path):
    target = tmp_path / "dup.md"
    target.write_text(VALID)

    files = collect_markdown_files([str(target), str(target), str(tmp_path)])

    assert files == [target]


def test_collect_markdown_files_no_matches(tmp_path: Path):
    files = collect_markdown_files([str(tmp_path / "no-such-file.md")])

    assert files == []
