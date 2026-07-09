import shutil
from pathlib import Path

import pytest

FIXTURE_KB = Path(__file__).parent / "fixtures" / "kb"


@pytest.fixture
def kb_path(tmp_path: Path) -> Path:
    """A throwaway copy of the fixture knowledge base."""
    target = tmp_path / "kb"
    shutil.copytree(FIXTURE_KB, target)
    return target
