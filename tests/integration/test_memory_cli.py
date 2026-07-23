from pathlib import Path

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from wakil.app.workspace_service import open_session
from wakil.cli.main import app
from wakil.config.settings import WorkspaceConfig
from wakil.storage.schema import Memory, User, Workspace

runner = CliRunner()


@pytest.fixture
def kb_with_memories(kb_path: Path) -> Path:
    runner.invoke(app, ["init", str(kb_path)])
    config = WorkspaceConfig.load(kb_path)
    with open_session(config) as session:
        ws = session.scalar(select(Workspace.id))
        user = session.scalar(select(User.id))
        session.add_all(
            [
                Memory(
                    workspace_id=ws,
                    user_id=user,
                    memory_type="decision",
                    content="Prototype FNOL routing.",
                    state="candidate",
                ),
                Memory(
                    workspace_id=ws,
                    user_id=user,
                    memory_type="fact",
                    content="Jane owns routing design.",
                    state="working",
                ),
                Memory(
                    workspace_id=ws,
                    user_id=user,
                    memory_type="opinion",
                    content="The FNOL routing budget seems too tight.",
                    state="candidate",
                ),
            ]
        )
        session.commit()
    return kb_path


def test_memory_list(kb_with_memories):
    result = runner.invoke(app, ["-w", str(kb_with_memories), "memory", "list"])
    assert result.exit_code == 0
    assert "Prototype FNOL routing." in result.output
    assert "candidate" in result.output

    result = runner.invoke(
        app, ["-w", str(kb_with_memories), "memory", "list", "--state", "working"]
    )
    assert "Jane owns routing design." in result.output
    assert "Prototype FNOL routing." not in result.output

    result = runner.invoke(
        app, ["-w", str(kb_with_memories), "memory", "list", "--type", "opinion"]
    )
    assert "The FNOL routing budget seems too tight." in result.output
    assert "Prototype FNOL routing." not in result.output


def test_memory_show(kb_with_memories):
    result = runner.invoke(app, ["-w", str(kb_with_memories), "memory", "show", "1"])
    assert result.exit_code == 0
    assert "Memory #1" in result.output
    assert "Prototype FNOL routing." in result.output

    result = runner.invoke(app, ["-w", str(kb_with_memories), "memory", "show", "99"])
    assert result.exit_code == 1
    assert "No memory with id" in result.output


def test_memory_promote_and_archive(kb_with_memories):
    result = runner.invoke(app, ["-w", str(kb_with_memories), "memory", "promote", "1", "2"])
    assert result.exit_code == 0, result.output
    assert "durable" in result.output

    config = WorkspaceConfig.load(kb_with_memories)
    with open_session(config) as session:
        states = {m.id: m.state for m in session.scalars(select(Memory))}
    assert states == {1: "durable", 2: "durable", 3: "candidate"}

    result = runner.invoke(app, ["-w", str(kb_with_memories), "memory", "archive", "1"])
    assert result.exit_code == 0
    with open_session(config) as session:
        assert session.get(Memory, 1).state == "archived"


def test_memory_reject_invalid_transition(kb_with_memories):
    runner.invoke(app, ["-w", str(kb_with_memories), "memory", "promote", "1"])
    result = runner.invoke(app, ["-w", str(kb_with_memories), "memory", "reject", "1"])
    assert result.exit_code == 1
    assert "cannot move" in result.output


def test_memory_list_empty(kb_path):
    runner.invoke(app, ["init", str(kb_path)])
    result = runner.invoke(app, ["-w", str(kb_path), "memory", "list"])
    assert result.exit_code == 0
    assert "No memories match" in result.output
