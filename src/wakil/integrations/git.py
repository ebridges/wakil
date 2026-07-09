"""Thin subprocess wrapper around git for workspace awareness."""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GitInfo:
    is_repo: bool
    branch: str | None = None
    is_dirty: bool = False
    remote_url: str | None = None
    recent_commits: list[str] = field(default_factory=list)


def _run_git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def inspect_git(root: Path) -> GitInfo:
    inside = _run_git(root, "rev-parse", "--is-inside-work-tree")
    if inside != "true":
        return GitInfo(is_repo=False)

    branch = _run_git(root, "rev-parse", "--abbrev-ref", "HEAD")
    porcelain = _run_git(root, "status", "--porcelain")
    remote = _run_git(root, "remote", "get-url", "origin")
    log = _run_git(root, "log", "--oneline", "-5")
    return GitInfo(
        is_repo=True,
        branch=branch,
        is_dirty=bool(porcelain),
        remote_url=remote,
        recent_commits=log.splitlines() if log else [],
    )
