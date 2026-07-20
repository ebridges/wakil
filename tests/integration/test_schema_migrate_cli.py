import subprocess
from pathlib import Path

from typer.testing import CliRunner

from wakil.cli.main import app

runner = CliRunner()

FIXABLE = (
    "---\ntype: person\nname: Old Colleague\nstatus: former\nend_date: 2025-01-31\n"
    "created: 2024-01-01\nupdated: 2025-01-31\n---\n\n# Old Colleague\n"
)


def _seed(kb_path: Path) -> Path:
    target = kb_path / "people" / "old-colleague.md"
    target.write_text(FIXABLE)
    runner.invoke(app, ["init", str(kb_path)])
    return target


def test_migrate_reports_clean_workspace(kb_path: Path):
    runner.invoke(app, ["init", str(kb_path)])
    result = runner.invoke(app, ["-w", str(kb_path), "schema", "migrate"])
    assert result.exit_code == 0
    assert "nothing to migrate" in result.output


def test_migrate_dry_run_shows_diff_and_writes_nothing(kb_path: Path):
    target = _seed(kb_path)
    result = runner.invoke(app, ["-w", str(kb_path), "schema", "migrate", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "person" in result.output
    assert "end_date" in result.output.replace("\n", "")  # diff shown
    assert "nothing was written" in result.output.lower()
    assert target.read_text() == FIXABLE


def test_migrate_declining_prompt_writes_nothing(kb_path: Path):
    target = _seed(kb_path)
    result = runner.invoke(app, ["-w", str(kb_path), "schema", "migrate"], input="n\n")
    assert result.exit_code == 0
    assert "Skipped person" in result.output
    assert target.read_text() == FIXABLE


def test_migrate_yes_applies(kb_path: Path):
    target = _seed(kb_path)
    result = runner.invoke(app, ["-w", str(kb_path), "schema", "migrate", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Rewrote 1 person file(s)" in result.output
    assert "end-date: 2025-01-31" in target.read_text()


def test_migrate_confirm_applies(kb_path: Path):
    target = _seed(kb_path)
    result = runner.invoke(app, ["-w", str(kb_path), "schema", "migrate"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "end-date: 2025-01-31" in target.read_text()


def test_migrate_with_commit(kb_path: Path):
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=kb_path, check=True)
    subprocess.run(["git", "-C", str(kb_path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(kb_path), "config", "user.name", "T"], check=True)
    subprocess.run(["git", "-C", str(kb_path), "config", "commit.gpgsign", "false"], check=True)
    _seed(kb_path)
    subprocess.run(["git", "-C", str(kb_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(kb_path), "commit", "-q", "-m", "seed"], check=True)

    result = runner.invoke(app, ["-w", str(kb_path), "schema", "migrate", "--yes", "--commit"])
    assert result.exit_code == 0, result.output
    subject = subprocess.run(
        ["git", "-C", str(kb_path), "log", "-1", "--format=%s"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert subject == "wakil chore: normalize person frontmatter"


def test_migrate_unknown_type_filter_fails(kb_path: Path):
    runner.invoke(app, ["init", str(kb_path)])
    result = runner.invoke(app, ["-w", str(kb_path), "schema", "migrate", "--type", "daily-note"])
    assert result.exit_code == 1
    assert "No entity schema" in result.output
