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

from datetime import datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    # IANA name (e.g. "America/New_York"). None means the machine's local
    # zone, which is what "today" means for a single-user local tool. Only
    # worth setting when wakil runs on a host in a different zone than the
    # person using it. See `workspace_today`.
    timezone: str | None = Field(default=None)

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
        # state_root is always set by _default_state_root above; still
        # typed `Path | None` since the field itself allows it pre-validation.
        assert self.state_root is not None
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


def workspace_today(config: WorkspaceConfig) -> str:
    """The user's calendar date, as an ISO string.

    This is what every user-visible date means: `created`/`captured`/
    `retrieved` frontmatter, the date prefix on a raw capture's filename, the
    date in a `wakil/ingest/<date>-<slug>` branch name, Timeline entry
    headings, and the "today" passed into the capture-metadata prompt.

    It is deliberately *not* UTC. Everything used to be `datetime.now(UTC)`,
    so an ingest run at 20:49 US-Eastern was stamped with tomorrow's date --
    four consecutive evening captures in one session all came out a day ahead
    of the meetings they recorded (#174).

    Note the distinction this draws: **instants stay UTC, calendar dates are
    local.** `storage/schema.py`'s `utcnow()` still backs every `created_at`/
    `retrieved_at`/`last_seen_at` column, because those are timestamps for
    ordering and age arithmetic (`memory_service.retrieval_rank` depends on
    it), not dates a human reads.
    """
    return datetime.now(_workspace_zone(config)).date().isoformat()


def _workspace_zone(config: WorkspaceConfig) -> tzinfo | None:
    """`None` means "the machine's local zone" to `datetime.now`, which is the
    default we want. An unknown IANA name falls back to local rather than
    failing a capture over a config typo."""
    if not config.timezone:
        return None
    try:
        return ZoneInfo(config.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return None


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
