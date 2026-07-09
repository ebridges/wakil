from pathlib import Path

from typer.testing import CliRunner

from wakil.cli.main import app

runner = CliRunner()


def test_search_finds_notes_via_fts(kb_path: Path):
    runner.invoke(app, ["init", str(kb_path)])
    result = runner.invoke(app, ["-w", str(kb_path), "search", "graph memory"])
    assert result.exit_code == 0
    assert "graph-memory.md" in result.output.replace("\n", "")


def test_search_reports_no_results(kb_path: Path):
    runner.invoke(app, ["init", str(kb_path)])
    result = runner.invoke(app, ["-w", str(kb_path), "search", "xyzzy plugh"])
    assert result.exit_code == 0
    assert "No results" in result.output


def test_search_without_workspace_fails(tmp_path: Path):
    result = runner.invoke(app, ["-w", str(tmp_path), "search", "anything"])
    assert result.exit_code == 1


def test_query_without_provider_fails_cleanly(kb_path: Path, monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "WAKIL_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    runner.invoke(app, ["init", str(kb_path)])
    result = runner.invoke(app, ["-w", str(kb_path), "query", "anything"])
    assert result.exit_code == 1
    assert "No model provider configured" in result.output


def test_query_answers_with_citations(kb_path: Path, monkeypatch):
    class FakeClient:
        model = "fake-model"

        def complete(self, system, prompt, max_tokens=8192):
            return "Graph memory relates to claims routing [1]."

    monkeypatch.setattr("wakil.llm.client.resolve_client", lambda: FakeClient())
    runner.invoke(app, ["init", str(kb_path)])
    result = runner.invoke(app, ["-w", str(kb_path), "query", "graph memory"])
    assert result.exit_code == 0
    assert "Graph memory relates to claims routing" in result.output
    assert "Citations" in result.output
