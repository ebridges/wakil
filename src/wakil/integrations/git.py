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


@dataclass
class WorktreeAnchors:
    toplevel: Path  # this checkout's own top-level directory
    common_dir: Path  # the shared .git dir -- identical across all linked worktrees


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


def create_branch_from(root: Path, name: str, base: str | None) -> None:
    """Create and switch to `name`, branching from `base` (a local branch
    name, e.g. the repo's default branch) rather than whatever is currently
    checked out. Falls back to branching from current HEAD when `base` is
    None (no resolvable default branch)."""
    if base is None:
        _run_git_checked(root, "switch", "-c", name)
        return
    remote_ref = f"origin/{base}"
    _run_git(root, "fetch", "origin", base)  # best-effort refresh; ignore failure
    if _run_git(root, "rev-parse", "--verify", remote_ref) is not None:
        _run_git_checked(root, "switch", "-c", name, remote_ref)
    else:
        _run_git_checked(root, "switch", "-c", name, base)


def branch_exists(root: Path, name: str) -> bool:
    return _run_git(root, "rev-parse", "--verify", f"refs/heads/{name}") is not None


def checkout(root: Path, name: str) -> None:
    _run_git_checked(root, "switch", name)


def checkout_new_tracking(root: Path, name: str) -> None:
    """Create a local branch `name` tracking the already-fetched
    `origin/<name>`."""
    _run_git_checked(root, "switch", "-c", name, f"origin/{name}")


def fetch_branch(root: Path, name: str) -> bool:
    """Fetch a single branch from origin into refs/remotes/origin/<name>.
    Returns False (never raises) if the branch doesn't exist on the remote
    or there is no remote — this is a existence probe as much as a fetch."""
    return _run_git(root, "fetch", "origin", f"{name}:refs/remotes/origin/{name}") is not None


def worktree_anchors(root: Path) -> WorktreeAnchors | None:
    """Both the current checkout's own top-level directory and the shared
    `.git` common-dir, in one call -- the common-dir is identical for a
    repo's main worktree and every `git worktree add`-linked one, which is
    what makes it a stable identity anchor across them. Returns None when
    `root` isn't inside a git repo at all."""
    output = _run_git(root, "rev-parse", "--show-toplevel", "--git-common-dir")
    if not output:
        return None
    lines = output.splitlines()
    if len(lines) != 2:
        return None
    toplevel, common_dir = lines
    return WorktreeAnchors(
        toplevel=Path(toplevel).resolve(), common_dir=(root / common_dir).resolve()
    )


def resolve_default_branch(root: Path) -> str | None:
    """Best-effort: the repo's default branch name ('main', 'master', ...),
    independent of whatever is currently checked out. Tries the remote's
    HEAD symref first, then falls back to common local branch names."""
    ref = _run_git(root, "symbolic-ref", "refs/remotes/origin/HEAD")
    if ref:
        return ref.rsplit("/", 1)[-1]
    for candidate in ("main", "master"):
        if branch_exists(root, candidate):
            return candidate
    return None


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


def branches_containing(root: Path, path: str) -> list[str]:
    """The `wakil/ingest/*` branches whose tree contains `path`.

    Enrichment resolves entity targets against the index/database, which
    knows about pages created by earlier sources, but writes against the
    working tree. When a cluster of related sources is captured in one
    sitting -- wakil's own branch-per-source model -- every source after the
    first can resolve to a page that exists only on an earlier, unmerged
    branch. Naming that branch is the difference between an actionable error
    and a silent no-op (#188).

    Display-only, so it degrades to `[]` rather than raising: this decorates
    a failure that has already happened.
    """
    found = []
    for branch in wakil_branches(root):
        if _run_git(root, "cat-file", "-e", f"{branch}:{path}") is not None:
            found.append(branch)
    return found
