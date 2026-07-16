from pathlib import Path

from typer.testing import CliRunner

from wakil.cli.main import app

runner = CliRunner()


def _write_skill(
    root: Path, name: str, *, skill_name: str | None = None, skill_api: int = 1
) -> Path:
    """Write a minimal skill at root/name/SKILL.md.

    Pass skill_name to deliberately mismatch the frontmatter name against the
    directory name, for negative validation tests.
    """
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    frontmatter_name = skill_name if skill_name is not None else name
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {frontmatter_name}\nskill_api: {skill_api}\n---\n\nBody.\n"
    )
    return skill_dir


def _user_skill_root(tmp_path: Path) -> Path:
    """Where a user-level skill lives, matching conftest's XDG_CONFIG_HOME isolation."""
    return tmp_path / "xdg-config" / "wakil" / "skills"


def test_skills_list_reports_kb_local_source(kb_path: Path):
    runner.invoke(app, ["init", str(kb_path)])
    _write_skill(kb_path / "skills", "meeting-synthesis")

    result = runner.invoke(app, ["-w", str(kb_path), "skills", "list"])
    assert result.exit_code == 0
    assert "meeting-synthesis" in result.output
    assert "kb-local" in result.output


def test_skills_list_with_no_skills(kb_path: Path):
    runner.invoke(app, ["init", str(kb_path)])

    result = runner.invoke(app, ["-w", str(kb_path), "skills", "list"])
    assert result.exit_code == 0
    assert "No skills found" in result.output


def test_skills_which_reports_selected_path(kb_path: Path):
    runner.invoke(app, ["init", str(kb_path)])
    skill_dir = _write_skill(kb_path / "skills", "meeting-synthesis")

    result = runner.invoke(app, ["-w", str(kb_path), "skills", "which", "meeting-synthesis"])
    assert result.exit_code == 0
    assert str(skill_dir / "SKILL.md") in result.output


def test_skills_which_verbose_shows_roots_and_shadows(kb_path: Path, tmp_path: Path):
    runner.invoke(app, ["init", str(kb_path)])
    _write_skill(kb_path / "skills", "meeting-synthesis")
    user_root = _user_skill_root(tmp_path)
    _write_skill(user_root, "meeting-synthesis")

    result = runner.invoke(
        app, ["-w", str(kb_path), "skills", "which", "meeting-synthesis", "--verbose"]
    )
    assert result.exit_code == 0
    assert "Search roots" in result.output
    assert "Shadowed matches" in result.output
    assert str(user_root / "meeting-synthesis") in result.output
    assert "winner" in result.output


def test_skills_which_unknown_name_fails(kb_path: Path):
    runner.invoke(app, ["init", str(kb_path)])

    result = runner.invoke(app, ["-w", str(kb_path), "skills", "which", "no-such-skill"])
    assert result.exit_code == 1
    assert "not_found" in result.output


def test_skills_validate_all_valid(kb_path: Path):
    runner.invoke(app, ["init", str(kb_path)])
    _write_skill(kb_path / "skills", "meeting-synthesis")

    result = runner.invoke(app, ["-w", str(kb_path), "skills", "validate"])
    assert result.exit_code == 0
    assert "All skills valid" in result.output


def test_skills_validate_reports_mismatched_metadata_name(kb_path: Path):
    runner.invoke(app, ["init", str(kb_path)])
    _write_skill(kb_path / "skills", "meeting-synthesis", skill_name="wrong-name")

    result = runner.invoke(app, ["-w", str(kb_path), "skills", "validate"])
    assert result.exit_code == 1
    assert "meeting-synthesis" in result.output
    assert "invalid_metadata" in result.output


def test_skills_validate_reports_missing_skill_md(kb_path: Path):
    runner.invoke(app, ["init", str(kb_path)])
    (kb_path / "skills" / "broken-skill").mkdir(parents=True)

    result = runner.invoke(app, ["-w", str(kb_path), "skills", "validate"])
    assert result.exit_code == 1
    assert "broken-skill" in result.output
    assert "invalid_directory" in result.output


def test_skills_validate_single_name(kb_path: Path):
    runner.invoke(app, ["init", str(kb_path)])
    _write_skill(kb_path / "skills", "meeting-synthesis")

    result = runner.invoke(app, ["-w", str(kb_path), "skills", "validate", "meeting-synthesis"])
    assert result.exit_code == 0
    assert "meeting-synthesis" in result.output


def test_skills_which_warns_on_missing_explicit_root(kb_path: Path, monkeypatch):
    runner.invoke(app, ["init", str(kb_path)])
    _write_skill(kb_path / "skills", "meeting-synthesis")
    monkeypatch.setenv("WAKIL_SKILL_PATH", str(kb_path / "nonexistent-skill-root"))

    result = runner.invoke(app, ["-w", str(kb_path), "skills", "which", "meeting-synthesis"])
    assert result.exit_code == 0
    assert "Warning" in result.output
    assert "missing" in result.output


def test_skills_validate_reports_missing_explicit_root(kb_path: Path, monkeypatch):
    runner.invoke(app, ["init", str(kb_path)])
    monkeypatch.setenv("WAKIL_SKILL_PATH", str(kb_path / "nonexistent-skill-root"))

    result = runner.invoke(app, ["-w", str(kb_path), "skills", "validate"])
    assert result.exit_code == 1
    assert "missing" in result.output


def test_skills_list_user_level_shadowed_by_kb_local(kb_path: Path, tmp_path: Path):
    runner.invoke(app, ["init", str(kb_path)])
    user_root = _user_skill_root(tmp_path)
    _write_skill(user_root, "entity-resolution")

    result = runner.invoke(app, ["-w", str(kb_path), "skills", "list"])
    assert result.exit_code == 0
    assert "entity-resolution" in result.output
    assert "user" in result.output

    # A kb-local skill of the same name shadows and wins over the user-level one.
    kb_local_dir = _write_skill(kb_path / "skills", "entity-resolution")
    result = runner.invoke(app, ["-w", str(kb_path), "skills", "which", "entity-resolution"])
    assert result.exit_code == 0
    assert str(kb_local_dir / "SKILL.md") in result.output
