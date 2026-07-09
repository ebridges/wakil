"""QMD detection.

QMD is the first-class search engine for the knowledge base. Phase 1 only
detects whether it is available; the search wrapper comes with Phase 2.
"""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class QmdInfo:
    available: bool
    version: str | None = None
    workspace_scripts: list[str] | None = None


def detect_qmd(root: Path | None = None) -> QmdInfo:
    binary = shutil.which("qmd")
    version: str | None = None
    if binary:
        try:
            result = subprocess.run(
                [binary, "--version"], capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                version = result.stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            pass

    scripts: list[str] | None = None
    if root is not None:
        bin_dir = root / "bin"
        if bin_dir.is_dir():
            found = sorted(p.name for p in bin_dir.glob("qmd-*"))
            scripts = found or None

    return QmdInfo(available=binary is not None, version=version, workspace_scripts=scripts)
