"""QMD integration: detection, search, and collection management.

QMD (https://github.com/tobi/qmd) is the first-class search engine for the
knowledge base. It offers three search modes: `search` (BM25 keyword),
`vsearch` (vector similarity), and `query` (hybrid with LLM re-ranking).
Results are requested as JSON so they map back to notes.

QMD's own index and collection config are scoped to each wakil workspace via
the `QMD_CONFIG_DIR`/`INDEX_PATH` environment variables it already respects,
pointed at `<workspace>/.wakil/qmd/` — a sibling of `wakil.db`, not the same
physical file (qmd manages its own SQLite schema via an independent process
with no locking coordination with wakil's SQLAlchemy connection).
"""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

SEARCH_MODES = ("search", "vsearch", "query")
DEFAULT_PATTERN = "**/*.md"


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


@dataclass
class QmdCollection:
    name: str
    path: Path
    pattern: str


@dataclass
class QmdCommandResult:
    success: bool
    message: str


def _qmd_env(qmd_dir: Path) -> dict[str, str]:
    return {
        **os.environ,
        "QMD_CONFIG_DIR": str(qmd_dir),
        "INDEX_PATH": str(qmd_dir / "index.sqlite"),
    }


def qmd_search(
    root: Path, qmd_dir: Path, query: str, limit: int = 10, mode: str = "search"
) -> list[QmdResult]:
    """Run a QMD search from the workspace root; empty list if unavailable."""
    if mode not in SEARCH_MODES:
        raise ValueError(f"unknown qmd mode: {mode}")
    if shutil.which("qmd") is None:
        return []
    try:
        result = subprocess.run(
            ["qmd", mode, query, "--json", "-n", str(limit)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=root,
            env=_qmd_env(qmd_dir),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return parse_qmd_results(result.stdout, root, qmd_dir)


def parse_qmd_results(output: str, root: Path, qmd_dir: Path) -> list[QmdResult]:
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

    collections = _read_collections(qmd_dir)
    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_path = item.get("file") or item.get("path") or item.get("filepath") or ""
        if not raw_path:
            continue
        score = _as_float(item.get("score"))
        if score is None:
            score = _as_float(item.get("relevance"))
        results.append(
            QmdResult(
                path=_relativize(str(raw_path), root, collections),
                score=score,
                snippet=str(
                    item.get("snippet") or item.get("excerpt") or item.get("context") or ""
                ),
                title=item.get("title"),
                docid=item.get("docid") or item.get("id"),
            )
        )
    return results


def _read_collections(qmd_dir: Path) -> dict[str, Path]:
    """Read qmd's own YAML collections config for name -> registered path."""
    config_path = qmd_dir / "index.yml"
    if not config_path.is_file():
        return {}
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    collections = data.get("collections") or {}
    if not isinstance(collections, dict):
        return {}
    result = {}
    for name, entry in collections.items():
        if isinstance(entry, dict) and "path" in entry:
            result[name] = Path(entry["path"])
    return result


def _relativize(raw_path: str, root: Path, collections: dict[str, Path]) -> str:
    if raw_path.startswith("qmd://"):
        rest = raw_path.removeprefix("qmd://")
        name, _, rel = rest.partition("/")
        base = collections.get(name)
        if base is not None:
            try:
                return str((base / rel).resolve().relative_to(root.resolve()))
            except ValueError:
                pass
        return rest
    try:
        return str(Path(raw_path).resolve().relative_to(root.resolve()))
    except ValueError:
        return raw_path


def _as_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def detect_qmd(root: Path | None = None, qmd_dir: Path | None = None) -> QmdInfo:
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

    project_index = qmd_dir is not None and (qmd_dir / "index.sqlite").exists()

    return QmdInfo(
        available=binary is not None,
        version=version,
        workspace_scripts=scripts,
        project_index=project_index,
    )


def qmd_list_collections(qmd_dir: Path) -> list[QmdCollection]:
    """List registered collections by reading qmd's own YAML config directly
    (qmd's `collection list` has no JSON output mode to parse reliably)."""
    config_path = qmd_dir / "index.yml"
    if not config_path.is_file():
        return []
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return []
    collections = data.get("collections") or {}
    if not isinstance(collections, dict):
        return []
    return [
        QmdCollection(
            name=name,
            path=Path(entry.get("path", "")),
            pattern=entry.get("pattern", DEFAULT_PATTERN),
        )
        for name, entry in collections.items()
        if isinstance(entry, dict)
    ]


def qmd_add_collection(
    root: Path,
    qmd_dir: Path,
    path: Path,
    name: str | None = None,
    pattern: str | None = None,
) -> QmdCommandResult:
    """Register `path` as a qmd collection, scoped to this workspace's index."""
    if shutil.which("qmd") is None:
        return QmdCommandResult(success=False, message="qmd is not installed (not found on PATH)")
    cmd = ["qmd", "collection", "add", str(path), "--mask", pattern or DEFAULT_PATTERN]
    if name:
        cmd += ["--name", name]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=root,
            env=_qmd_env(qmd_dir),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return QmdCommandResult(success=False, message=str(exc))
    output = (result.stdout or result.stderr or "").strip()
    return QmdCommandResult(success=result.returncode == 0, message=output)


def qmd_remove_collection(qmd_dir: Path, name: str) -> QmdCommandResult:
    if shutil.which("qmd") is None:
        return QmdCommandResult(success=False, message="qmd is not installed (not found on PATH)")
    try:
        result = subprocess.run(
            ["qmd", "collection", "remove", name],
            capture_output=True,
            text=True,
            timeout=120,
            env=_qmd_env(qmd_dir),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return QmdCommandResult(success=False, message=str(exc))
    output = (result.stdout or result.stderr or "").strip()
    return QmdCommandResult(success=result.returncode == 0, message=output)


def qmd_update(qmd_dir: Path, root: Path | None = None) -> QmdCommandResult:
    """Re-scan every registered collection for new/changed/removed files."""
    if shutil.which("qmd") is None:
        return QmdCommandResult(success=False, message="qmd is not installed (not found on PATH)")
    try:
        result = subprocess.run(
            ["qmd", "update"],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=root,
            env=_qmd_env(qmd_dir),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return QmdCommandResult(success=False, message=str(exc))
    output = (result.stdout or result.stderr or "").strip()
    return QmdCommandResult(success=result.returncode == 0, message=output)


def qmd_embed(qmd_dir: Path, root: Path | None = None) -> QmdCommandResult:
    """Generate embeddings for any indexed content that doesn't have one yet.

    Only content already known to qmd's index gets embedded — run
    `qmd_update` first so newly ingested files are indexed before this looks
    for what needs a vector. The first call ever made against a fresh model
    cache downloads the embedding model from Hugging Face (which is why the
    timeout here is generous), and both that download and the embedding pass
    render qmd's own live progress bar. Output is deliberately NOT captured
    here (unlike the other qmd_* calls) so that progress bar streams straight
    to the terminal instead of being silently buffered until the process
    exits — a multi-minute wait with zero feedback reads as a hang.
    """
    if shutil.which("qmd") is None:
        return QmdCommandResult(success=False, message="qmd is not installed (not found on PATH)")
    try:
        result = subprocess.run(
            ["qmd", "embed"],
            timeout=3600,
            cwd=root,
            env=_qmd_env(qmd_dir),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return QmdCommandResult(success=False, message=str(exc))
    output = (result.stdout or result.stderr or "").strip()
    return QmdCommandResult(success=result.returncode == 0, message=output)
