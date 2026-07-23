"""CLI shape for `wakil relationships`.

Exercises the traversal command end-to-end: init a small kb with real
`[[wikilink]]`s, invoke the CLI, and assert the rendered table shape.
"""

from pathlib import Path

from typer.testing import CliRunner

from wakil.cli.main import app

runner = CliRunner()


def _init_kb(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "people").mkdir(exist_ok=True)
    (root / "sources").mkdir(exist_ok=True)
    (root / "people" / "alice.md").write_text(
        "# Alice\n\nMet [[people/bob]] and cited [[sources/paper.md]].\n"
    )
    (root / "people" / "bob.md").write_text(
        "# Bob\n\nColleague of [[people/alice]].\n"
    )
    (root / "sources" / "paper.md").write_text("# Paper\n")
    result = runner.invoke(app, ["init", str(root)])
    assert result.exit_code == 0, result.output


def test_outgoing_traversal_lists_targets(tmp_path: Path):
    kb = tmp_path / "kb"
    _init_kb(kb)

    result = runner.invoke(
        app, ["-w", str(kb), "relationships", "people/alice.md", "--direction", "out"]
    )
    assert result.exit_code == 0, result.output
    assert "people/bob.md" in result.output
    assert "sources/paper.md" in result.output
    assert "mentions" in result.output


def test_incoming_traversal_finds_backlinks(tmp_path: Path):
    kb = tmp_path / "kb"
    _init_kb(kb)

    result = runner.invoke(
        app, ["-w", str(kb), "relationships", "people/alice.md", "--direction", "in"]
    )
    assert result.exit_code == 0, result.output
    assert "people/bob.md" in result.output  # bob links to alice
    # A backlink query into alice.md must not surface alice's own outgoing targets.
    assert "sources/paper.md" not in result.output


def test_predicate_filter_narrows_results(tmp_path: Path):
    kb = tmp_path / "kb"
    _init_kb(kb)

    result = runner.invoke(
        app,
        [
            "-w",
            str(kb),
            "relationships",
            "people/alice.md",
            "--direction",
            "out",
            "--predicate",
            "nonexistent-predicate",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "No related notes" in result.output


def test_bad_direction_exits_with_error(tmp_path: Path):
    kb = tmp_path / "kb"
    _init_kb(kb)

    result = runner.invoke(
        app,
        ["-w", str(kb), "relationships", "people/alice.md", "--direction", "sideways"],
    )
    assert result.exit_code == 2, result.output
    assert "direction" in result.output


def test_missing_anchor_note_exits_with_error(tmp_path: Path):
    kb = tmp_path / "kb"
    _init_kb(kb)

    result = runner.invoke(
        app, ["-w", str(kb), "relationships", "does/not/exist.md"]
    )
    assert result.exit_code == 1, result.output
    assert "no note" in result.output
