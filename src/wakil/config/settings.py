"""Workspace configuration: where wakil state lives inside a knowledge base.

All wakil state for a workspace is kept in a `.wakil/` directory: a YAML
config file and a SQLite database. Markdown files remain the source of
truth; `.wakil/` is operational state only.

`.wakil/` normally lives at the knowledge-base root. The one exception is a
linked git worktree (`git worktree add`): its `root_path` — where its
checked-out files actually live, used for all file I/O — is its own
directory, but its `state_root` — where `.wakil/` and the workspace's
identity in the database live — resolves to the *main* worktree's root, via
the `.git` common-dir shared by every worktree of one repo. This is what
lets several worktrees of the same repo (e.g. one per concurrent ingest)
share one workspace — one set of sources, one content-hash dedup index, one
FTS/QMD index — instead of each silently getting its own, empty one.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from wakil.integrations.git import worktree_anchors

WAKIL_DIR = ".wakil"
CONFIG_FILENAME = "config.yaml"
DATABASE_FILENAME = "wakil.db"
QMD_DIRNAME = "qmd"

# Top-level files treated as high-priority workspace context when present.
SPECIAL_FILES = ("README.md", "AGENTS.md", "RESOLVER.md")


def resolve_state_root(root: Path) -> Path:
    """Where .wakil/ actually lives for a checkout at `root`: the main
    worktree's root for a linked git worktree, or `root` itself otherwise
    (not a git repo, or already the main worktree)."""
    anchors = worktree_anchors(root)
    if anchors is None:
        return root
    return anchors.common_dir.parent


class WorkspaceConfig(BaseModel):
    """Persisted per-workspace settings.

    `root_path` is this checkout's own directory — used for all file I/O
    (reading/writing notes, git operations on this checkout). `state_root`
    is where `.wakil/` lives and is the identity a `Workspace` database row
    is keyed on; it defaults to `root_path` and only differs for a linked
    git worktree (see module docstring).
    """

    name: str
    root_path: Path
    state_root: Path | None = None
    git_remote: str | None = None
    qmd_enabled: bool = False
    ingest_directory: str = Field(default="sources")
    generated_directory: str = Field(default="drafts")

    @model_validator(mode="after")
    def _default_state_root(self) -> WorkspaceConfig:
        if self.state_root is None:
            self.state_root = self.root_path
        return self

    @property
    def is_linked_worktree(self) -> bool:
        return self.root_path != self.state_root

    @property
    def wakil_dir(self) -> Path:
        return self.state_root / WAKIL_DIR

    @property
    def database_path(self) -> Path:
        return self.wakil_dir / DATABASE_FILENAME

    @property
    def config_path(self) -> Path:
        return self.wakil_dir / CONFIG_FILENAME

    @property
    def qmd_dir(self) -> Path:
        """Where this workspace's QMD index/collections live (separate file
        from wakil.db — qmd manages its own SQLite schema via an independent
        process with no locking coordination with wakil's connection)."""
        return self.wakil_dir / QMD_DIRNAME

    def save(self) -> None:
        self.wakil_dir.mkdir(parents=True, exist_ok=True)
        # Operational state stays out of the knowledge base's git history.
        gitignore = self.wakil_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n", encoding="utf-8")
        data = self.model_dump(mode="json")
        self.config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    @classmethod
    def load(cls, root: Path) -> WorkspaceConfig:
        state_root = resolve_state_root(root)
        config_path = state_root / WAKIL_DIR / CONFIG_FILENAME
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        # The workspace may have been moved or checked out elsewhere, or
        # `root` may be a different worktree of the same repo; trust the
        # directories resolved for *this* invocation over whatever was
        # persisted.
        data["root_path"] = str(root)
        data["state_root"] = str(state_root)
        return cls.model_validate(data)


def is_initialized(root: Path) -> bool:
    # Fast path: .wakil/ lives directly here -- true for the overwhelming
    # majority of workspaces (no worktrees involved), with zero subprocess
    # overhead.
    if (root / WAKIL_DIR / CONFIG_FILENAME).is_file():
        return True
    # Slow path: a linked git worktree shares its main worktree's .wakil/,
    # which the direct check above won't find.
    anchors = worktree_anchors(root)
    if anchors is None or root.resolve() != anchors.toplevel:
        return False
    return (anchors.common_dir.parent / WAKIL_DIR / CONFIG_FILENAME).is_file()


def find_workspace_root(start: Path) -> Path | None:
    """Walk upward from `start` looking for an initialized workspace."""
    current = start.resolve()
    for candidate in (current, *current.parents):
        if is_initialized(candidate):
            return candidate
    return None
