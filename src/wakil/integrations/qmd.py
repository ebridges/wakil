"""QMD integration: detection and search.

QMD (https://github.com/tobi/qmd) is the first-class search engine for the
knowledge base. It offers three modes: `search` (BM25 keyword), `vsearch`
(vector similarity), and `query` (hybrid with LLM re-ranking). Results are
requested as JSON with filesystem paths so they map back to notes.
"""

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SEARCH_MODES = ("search", "vsearch", "query")


@dataclass
class QmdInfo:
    available: bool
    version: str | None = None
    workspace_scripts: list[str] | None = None
    project_index: bool = False


@dataclass
class QmdResult:
    path: str
    score: float | None = None
    snippet: str = ""
    title: str | None = None
    docid: str | None = None


def qmd_search(root: Path, query: str, limit: int = 10, mode: str = "search") -> list[QmdResult]:
    """Run a QMD search from the workspace root; empty list if unavailable."""
    if mode not in SEARCH_MODES:
        raise ValueError(f"unknown qmd mode: {mode}")
    if shutil.which("qmd") is None:
        return []
    try:
        result = subprocess.run(
            ["qmd", mode, query, "--format", "json", "-n", str(limit), "--full-path"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=root,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return parse_qmd_results(result.stdout, root)


def parse_qmd_results(output: str, root: Path) -> list[QmdResult]:
    """Parse QMD JSON output defensively; malformed output yields no results."""
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        items = data.get("results") or data.get("documents") or []
    elif isinstance(data, list):
        items = data
    else:
        return []

    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_path = item.get("file") or item.get("path") or item.get("filepath") or ""
        if not raw_path:
            continue
        results.append(
            QmdResult(
                path=_relativize(str(raw_path), root),
                score=_as_float(item.get("score") or item.get("relevance")),
                snippet=str(
                    item.get("snippet") or item.get("excerpt") or item.get("context") or ""
                ),
                title=item.get("title"),
                docid=item.get("docid") or item.get("id"),
            )
        )
    return results


def _relativize(raw_path: str, root: Path) -> str:
    path = raw_path.removeprefix("qmd://")
    try:
        return str(Path(path).resolve().relative_to(root.resolve()))
    except ValueError:
        return path


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    project_index = False
    if root is not None:
        bin_dir = root / "bin"
        if bin_dir.is_dir():
            found = sorted(p.name for p in bin_dir.glob("qmd-*"))
            scripts = found or None
        project_index = (root / ".qmd").exists()

    return QmdInfo(
        available=binary is not None,
        version=version,
        workspace_scripts=scripts,
        project_index=project_index,
    )
