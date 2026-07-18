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
# CliRunner has no tty, so Rich falls back to an 80-column default. That's
# narrow enough that realistic cell content (snippets, diffs) wraps and
# splits words across box-drawing borders, breaking plain-text substring
# assertions. Pin a wider width so table output matches a normal terminal.
os.environ["COLUMNS"] = "200"

FIXTURE_KB = Path(__file__).parent / "fixtures" / "kb"


@pytest.fixture(autouse=True)
def isolated_workspace_registry(tmp_path: Path, monkeypatch):
    """Keep the workspace-name registry out of the real ~/.config."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.delenv("WAKIL_WORKSPACE", raising=False)
    monkeypatch.delenv("WAKIL_SKILL_PATH", raising=False)


@pytest.fixture(autouse=True)
def _no_real_qmd(monkeypatch):
    """Never shell out to a real qmd binary during tests, even if one happens
    to be installed on the dev machine — `wakil init`/ingest now auto-trigger
    qmd collection/embed subprocesses, which would otherwise make ordinary
    tests slow, network-dependent (model downloads), and non-deterministic.
    Tests that want to exercise qmd behavior re-enable it explicitly by
    monkeypatching `wakil.integrations.qmd.shutil.which` themselves (see
    test_qmd_cli.py's `_patch_qmd`), which overrides this default."""
    monkeypatch.setattr("wakil.integrations.qmd.shutil.which", lambda name: None)


@pytest.fixture
def kb_path(tmp_path: Path) -> Path:
    """A throwaway copy of the fixture knowledge base."""
    target = tmp_path / "kb"
    shutil.copytree(FIXTURE_KB, target)
    return target
