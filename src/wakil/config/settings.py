"""Workspace configuration: where wakil state lives inside a knowledge base.

All wakil state for a workspace is kept in a `.wakil/` directory at the
knowledge-base root: a YAML config file and a SQLite database. Markdown files
remain the source of truth; `.wakil/` is operational state only.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

WAKIL_DIR = ".wakil"
CONFIG_FILENAME = "config.yaml"
DATABASE_FILENAME = "wakil.db"
QMD_DIRNAME = "qmd"

# Top-level files treated as high-priority workspace context when present.
SPECIAL_FILES = ("README.md", "AGENTS.md", "SCHEMA.md", "RESOLVER.md")


class WorkspaceConfig(BaseModel):
    """Persisted per-workspace settings."""

    name: str
    root_path: Path
    git_remote: str | None = None
    qmd_enabled: bool = False
    ingest_directory: str = Field(default="sources")
    generated_directory: str = Field(default="drafts")

    @property
    def wakil_dir(self) -> Path:
        return self.root_path / WAKIL_DIR

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
        config_path = root / WAKIL_DIR / CONFIG_FILENAME
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        # The workspace may have been moved or checked out elsewhere; trust
        # the directory the config was found in over the stored path.
        data["root_path"] = str(root)
        return cls.model_validate(data)


def is_initialized(root: Path) -> bool:
    return (root / WAKIL_DIR / CONFIG_FILENAME).is_file()


def find_workspace_root(start: Path) -> Path | None:
    """Walk upward from `start` looking for an initialized workspace."""
    current = start.resolve()
    for candidate in (current, *current.parents):
        if is_initialized(candidate):
            return candidate
    return None
