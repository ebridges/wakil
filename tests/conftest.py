import os
import shutil
from pathlib import Path

import pytest

# Rich's Console (constructed at import time in wakil.ui.console) picks up
# ambient color-forcing env vars from the invoking shell. An inherited
# FORCE_COLOR causes ANSI codes to be interleaved into numeric output (Rich's
# automatic number highlighting), which breaks plain-text substring
# assertions like `"1 added" in result.output` even though the underlying
# behavior is correct. Normalize before any wakil module is imported so test
# output is deterministic regardless of the developer's terminal settings.
os.environ.pop("FORCE_COLOR", None)
os.environ["NO_COLOR"] = "1"

FIXTURE_KB = Path(__file__).parent / "fixtures" / "kb"


@pytest.fixture(autouse=True)
def isolated_workspace_registry(tmp_path: Path, monkeypatch):
    """Keep the workspace-name registry out of the real ~/.config."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.delenv("WAKIL_WORKSPACE", raising=False)
    monkeypatch.delenv("WAKIL_SKILL_PATH", raising=False)


@pytest.fixture
def kb_path(tmp_path: Path) -> Path:
    """A throwaway copy of the fixture knowledge base."""
    target = tmp_path / "kb"
    shutil.copytree(FIXTURE_KB, target)
    return target
