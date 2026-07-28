"""`wakil mcp serve` CLI wiring (docs/adr/0018).

The command blocks on a real stdio transport once it resolves a workspace,
so these tests only exercise argument parsing and workspace resolution —
not a live server round-trip (see mcp/tools.py's own direct unit tests for
tool behavior, and the plan's verification steps for a manual stdio check).
"""

from pathlib import Path

from typer.testing import CliRunner

from wakil.cli.main import app

runner = CliRunner()


def test_mcp_serve_help_describes_the_command():
    result = runner.invoke(app, ["mcp", "serve", "--help"])
    assert result.exit_code == 0
    assert "MCP server" in result.output


def test_mcp_serve_without_workspace_fails_cleanly(tmp_path: Path):
    result = runner.invoke(app, ["-w", str(tmp_path), "mcp", "serve"])
    assert result.exit_code == 1
    assert "not inside an initialized wakil workspace" in result.output
