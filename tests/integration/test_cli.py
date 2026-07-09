import subprocess
from pathlib import Path

from typer.testing import CliRunner

from wakil.cli.main import app

runner = CliRunner()


def test_init_and_status(kb_path: Path):
    result = runner.invoke(app, ["init", str(kb_path)])
    assert result.exit_code == 0
    assert "8 added" in result.output

    result = runner.invoke(app, ["-w", str(kb_path), "status"])
    assert result.exit_code == 0
    assert "kb" in result.output
    assert "8" in result.output


def test_status_from_subdirectory_finds_workspace(kb_path: Path):
    runner.invoke(app, ["init", str(kb_path)])
    result = runner.invoke(app, ["-w", str(kb_path / "concepts"), "status"])
    assert result.exit_code == 0


def test_status_defaults_to_cwd(kb_path: Path, monkeypatch):
    runner.invoke(app, ["init", str(kb_path)])
    monkeypatch.chdir(kb_path / "people")
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "kb" in result.output


def test_status_by_registered_name(kb_path: Path):
    runner.invoke(app, ["init", str(kb_path), "--name", "my-kb"])
    result = runner.invoke(app, ["-w", "my-kb", "status"])
    assert result.exit_code == 0
    assert "my-kb" in result.output


def test_workspace_env_var(kb_path: Path, monkeypatch):
    runner.invoke(app, ["init", str(kb_path), "--name", "env-kb"])
    monkeypatch.setenv("WAKIL_WORKSPACE", "env-kb")
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "env-kb" in result.output


def test_unknown_workspace_name_fails(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["-w", "nonexistent-name", "status"])
    assert result.exit_code == 1
    assert "Unknown workspace" in result.output


def test_uninitialized_directory_fails(tmp_path: Path):
    result = runner.invoke(app, ["-w", str(tmp_path), "status"])
    assert result.exit_code == 1
    assert "not inside an initialized" in " ".join(result.output.split())


def test_status_without_workspace_fails(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "No wakil workspace found" in result.output


def test_index_command_reports_changes(kb_path: Path):
    runner.invoke(app, ["init", str(kb_path)])
    (kb_path / "concepts" / "new-idea.md").write_text("# New Idea\n")

    result = runner.invoke(app, ["-w", str(kb_path), "index"])
    assert result.exit_code == 0
    assert "1 added" in result.output


def test_init_detects_git_repository(kb_path: Path):
    subprocess.run(["git", "init", "-q"], cwd=kb_path, check=True)

    result = runner.invoke(app, ["init", str(kb_path)])
    assert result.exit_code == 0
    assert "branch" in result.output


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "wakil" in result.output
