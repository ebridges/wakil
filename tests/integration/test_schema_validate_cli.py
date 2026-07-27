from pathlib import Path

from typer.testing import CliRunner

from wakil.cli.main import app

runner = CliRunner()

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


def _seed(kb_path: Path, filename: str, content: str) -> Path:
    target = kb_path / "people" / filename
    target.write_text(content)
    runner.invoke(app, ["init", str(kb_path)])
    return target


def test_validate_reports_no_errors_for_valid_file(kb_path: Path):
    target = _seed(kb_path, "valid-colleague.md", VALID)
    result = runner.invoke(app, ["-w", str(kb_path), "schema", "validate", str(target)])
    assert result.exit_code == 0, result.output
    assert "✓" in result.output
    assert "✗" not in result.output


def test_validate_reports_bad_enum_value_and_fails(kb_path: Path):
    target = _seed(kb_path, "bad-status.md", BAD_ENUM)
    result = runner.invoke(app, ["-w", str(kb_path), "schema", "validate", str(target)])
    assert result.exit_code == 1
    assert "✗" in result.output
    assert "status" in result.output
    assert "not-a-real-status" in result.output


def test_validate_reports_missing_required_field_and_fails(kb_path: Path):
    target = _seed(kb_path, "no-status.md", MISSING_REQUIRED)
    result = runner.invoke(app, ["-w", str(kb_path), "schema", "validate", str(target)])
    assert result.exit_code == 1
    assert "required field is missing" in result.output


def test_validate_accepts_multiple_paths(kb_path: Path):
    good = _seed(kb_path, "valid-colleague.md", VALID)
    bad = kb_path / "people" / "bad-status.md"
    bad.write_text(BAD_ENUM)
    result = runner.invoke(app, ["-w", str(kb_path), "schema", "validate", str(good), str(bad)])
    assert result.exit_code == 1
    assert "✓" in result.output
    assert "✗" in result.output


def test_validate_accepts_a_directory(kb_path: Path):
    _seed(kb_path, "valid-colleague.md", VALID)
    (kb_path / "people" / "bad-status.md").write_text(BAD_ENUM)
    result = runner.invoke(
        app, ["-w", str(kb_path), "schema", "validate", str(kb_path / "people")]
    )
    assert result.exit_code == 1
    assert "✓" in result.output
    assert "✗" in result.output


def test_validate_no_matching_files_exits_zero(kb_path: Path):
    runner.invoke(app, ["init", str(kb_path)])
    result = runner.invoke(
        app, ["-w", str(kb_path), "schema", "validate", str(kb_path / "no-such-dir")]
    )
    assert result.exit_code == 0
    assert "no files matched" in result.output.lower()
