"""Thin subprocess wrapper around git for workspace awareness and changes.

Read helpers return None/empty on failure (status display must never crash);
write helpers raise GitError so callers can surface what went wrong.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class GitError(RuntimeError):
    pass


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


def _run_git_checked(root: Path, *args: str, timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"git {args[0]} failed: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise GitError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def changed_files(root: Path) -> list[str]:
    """Porcelain status lines, e.g. ' M notes/a.md' or '?? drafts/new.md'."""
    porcelain = _run_git(root, "status", "--porcelain")
    return porcelain.splitlines() if porcelain else []


def create_branch(root: Path, name: str) -> None:
    _run_git_checked(root, "switch", "-c", name)


def branch_exists(root: Path, name: str) -> bool:
    return _run_git(root, "rev-parse", "--verify", f"refs/heads/{name}") is not None


def stage_and_commit(root: Path, paths: list[str], message: str) -> str:
    """Stage exactly `paths`, commit, and return the commit sha."""
    _run_git_checked(root, "add", "--", *paths)
    _run_git_checked(root, "commit", "-m", message, "--", *paths)
    return _run_git_checked(root, "rev-parse", "HEAD")


def push_branch(root: Path, name: str) -> None:
    _run_git_checked(root, "push", "-u", "origin", name, timeout=120)


def file_history(root: Path, path: str, limit: int = 10) -> list[str]:
    log = _run_git(
        root, "log", "--follow", f"-{limit}", "--format=%h %ad %s", "--date=short", "--", path
    )
    return log.splitlines() if log else []


def wakil_branches(root: Path) -> list[str]:
    out = _run_git(root, "branch", "--list", "wakil/*", "--format=%(refname:short)")
    return out.splitlines() if out else []


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
