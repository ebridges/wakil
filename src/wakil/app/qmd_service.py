"""QMD collection management: path validation and the default-collection strategy.

QMD's BM25 scoring runs over one shared FTS5 index regardless of collection
boundaries — collections are a filter applied after scoring, not separate
indexes with separate corpus statistics. Splitting the KB into many
per-folder/per-type collections therefore buys nothing for ranking quality,
and risks double-indexing files that fall under two overlapping directories.
The default strategy here is a single collection covering the whole
workspace; users who want to filter or exclude a folder can still add/remove
collections by hand via `wakil qmd collection add/remove`.

`refresh_index` re-scans collections and (re-)embeds anything new after
ingest, so search stays current without a separate manual step.
"""

from dataclasses import dataclass
from pathlib import Path

from wakil.config.settings import WorkspaceConfig
from wakil.integrations import qmd
from wakil.integrations.qmd import QmdCommandResult


@dataclass
class CollectionPlan:
    name: str
    path: str  # relative to workspace root
    pattern: str


class QmdPathError(Exception):
    """A collection path escapes the workspace root."""


def add_collection(
    config: WorkspaceConfig, path: Path, name: str | None = None, pattern: str | None = None
) -> QmdCommandResult:
    """Register `path` (relative to the workspace root, or already inside it)
    as a qmd collection. Rejects paths that resolve outside the workspace."""
    root = config.root_path.resolve()
    candidate = path if path.is_absolute() else root / path
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise QmdPathError(f"{path} resolves outside the workspace root {root}.") from exc

    config.qmd_dir.mkdir(parents=True, exist_ok=True)
    return qmd.qmd_add_collection(root, config.qmd_dir, candidate, name=name, pattern=pattern)


def plan_default_collections(config: WorkspaceConfig) -> list[CollectionPlan]:
    """Propose a single collection covering the whole workspace, but only when
    none are registered yet. Empty once any collection exists — there's
    nothing further to reconcile once a collection is registered."""
    if qmd.qmd_list_collections(config.qmd_dir):
        return []
    return [CollectionPlan(name=config.name, path=".", pattern=qmd.DEFAULT_PATTERN)]


def ensure_default_collection(config: WorkspaceConfig) -> QmdCommandResult | None:
    """Register the default whole-workspace collection if none exist yet.
    Returns None if there was nothing to do (a collection already exists)."""
    plans = plan_default_collections(config)
    if not plans:
        return None
    plan = plans[0]
    return add_collection(config, Path(plan.path), name=plan.name, pattern=plan.pattern)


def refresh_index(config: WorkspaceConfig) -> list[QmdCommandResult]:
    """Re-scan registered collections for file changes, then embed whatever
    still lacks a vector. A no-op (empty list) if qmd isn't available or no
    collection has been registered yet — nothing to refresh either way."""
    if not config.qmd_enabled or not qmd.qmd_list_collections(config.qmd_dir):
        return []
    update_result = qmd.qmd_update(config.qmd_dir, config.root_path)
    if not update_result.success:
        return [update_result]
    return [update_result, qmd.qmd_embed(config.qmd_dir, config.root_path)]
