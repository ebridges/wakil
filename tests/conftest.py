import shutil
from pathlib import Path

import pytest

FIXTURE_KB = Path(__file__).parent / "fixtures" / "kb"


@pytest.fixture(autouse=True)
def isolated_workspace_registry(tmp_path: Path, monkeypatch):
    """Keep the workspace-name registry out of the real ~/.config."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.delenv("WAKIL_WORKSPACE", raising=False)


@pytest.fixture
def kb_path(tmp_path: Path) -> Path:
    """A throwaway copy of the fixture knowledge base."""
    target = tmp_path / "kb"
    shutil.copytree(FIXTURE_KB, target)
    return target
