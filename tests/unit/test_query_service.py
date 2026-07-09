from pathlib import Path

import pytest
from sqlalchemy import select

from wakil.app.query_service import run_query
from wakil.app.workspace_service import init_workspace, open_session
from wakil.config.settings import WorkspaceConfig
from wakil.storage.schema import QueryRun


class FakeClient:
    model = "fake-model"

    def __init__(self, answer: str = "Grounded answer [1]."):
        self.answer = answer
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, prompt: str, max_tokens: int = 8192) -> str:
        self.calls.append((system, prompt))
        return self.answer


def test_query_grounds_answer_in_note_context(kb_path: Path):
    init_workspace(kb_path)
    config = WorkspaceConfig.load(kb_path)
    client = FakeClient()

    result = run_query(config, "How does graph memory relate to claims routing?", client)

    assert result.answer == "Grounded answer [1]."
    assert result.contexts, "expected matching notes to become context blocks"
    system, prompt = client.calls[0]
    assert "cite" in system.lower()
    assert "concepts/graph-memory.md" in prompt
    assert "How does graph memory relate to claims routing?" in prompt


def test_query_records_query_run(kb_path: Path):
    init_workspace(kb_path)
    config = WorkspaceConfig.load(kb_path)

    result = run_query(config, "graph memory", FakeClient())

    with open_session(config) as session:
        run = session.scalar(select(QueryRun))
    assert run is not None
    assert run.id == result.query_run_id
    assert run.status == "completed"
    assert run.answer == result.answer
    assert "concepts/graph-memory.md" in run.notes_used_json


def test_query_with_no_matches_still_calls_model(kb_path: Path):
    init_workspace(kb_path)
    config = WorkspaceConfig.load(kb_path)
    client = FakeClient(answer="The knowledge base has no material on this.")

    result = run_query(config, "xyzzy plugh nothing", client)

    assert result.contexts == []
    _, prompt = client.calls[0]
    assert "No matching context" in prompt


def test_query_records_error_when_model_fails(kb_path: Path):
    init_workspace(kb_path)
    config = WorkspaceConfig.load(kb_path)

    class ExplodingClient:
        model = "fake-model"

        def complete(self, system, prompt, max_tokens=8192):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        run_query(config, "graph memory", ExplodingClient())

    with open_session(config) as session:
        run = session.scalar(select(QueryRun))
    assert run.status == "error"
