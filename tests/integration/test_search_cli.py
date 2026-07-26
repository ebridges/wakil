from pathlib import Path

from sqlalchemy import select
from typer.testing import CliRunner

from wakil.app.workspace_service import open_session
from wakil.cli.main import app
from wakil.config.settings import WorkspaceConfig
from wakil.storage.schema import Memory, User, Workspace

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

        def complete(self, system, prompt, max_tokens=8192, *, cacheable_prefix=None):
            return "Graph memory relates to claims routing [1]."

    monkeypatch.setattr("wakil.llm.client.resolve_client", lambda: FakeClient())
    runner.invoke(app, ["init", str(kb_path)])
    result = runner.invoke(app, ["-w", str(kb_path), "query", "graph memory"])
    assert result.exit_code == 0
    assert "Graph memory relates to claims routing" in result.output
    assert "Citations" in result.output


def test_search_still_surfaces_casual_memories(kb_path: Path):
    """wakil search is unaffected by the query-only casual exclusion --
    hot takes still surface there (only `wakil query`'s grounding excludes
    them by default)."""
    runner.invoke(app, ["init", str(kb_path)])
    config = WorkspaceConfig.load(kb_path)
    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        user = session.scalar(select(User.id))
        session.add(
            Memory(
                workspace_id=ws,
                user_id=user,
                memory_type="fact",
                content="zugzwang casual metric for search",
                stance="casual",
            )
        )
        session.commit()

    result = runner.invoke(app, ["-w", str(kb_path), "search", "zugzwang"])

    assert result.exit_code == 0
    assert "zugzwang casual metric for search" in result.output.replace("\n", "")


def test_query_include_casual_flag(kb_path: Path, monkeypatch):
    class FakeClient:
        model = "fake-model"

        def complete(self, system, prompt, max_tokens=8192, *, cacheable_prefix=None):
            return "ok"

    monkeypatch.setattr("wakil.llm.client.resolve_client", lambda: FakeClient())
    runner.invoke(app, ["init", str(kb_path)])
    config = WorkspaceConfig.load(kb_path)
    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        user = session.scalar(select(User.id))
        session.add(
            Memory(
                workspace_id=ws,
                user_id=user,
                memory_type="fact",
                content="zugzwang casual metric",
                stance="casual",
            )
        )
        session.commit()

    result = runner.invoke(app, ["-w", str(kb_path), "query", "zugzwang"])
    assert result.exit_code == 0
    assert "Citations" not in result.output

    result = runner.invoke(app, ["-w", str(kb_path), "query", "zugzwang", "--include-casual"])
    assert result.exit_code == 0
    assert "Citations" in result.output
    assert "memory:" in result.output
