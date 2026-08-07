"""Thin subprocess wrapper around git for workspace awareness and changes.

Read helpers return None/empty on failure (status display must never crash);
write helpers raise GitError so callers can surface what went wrong.

That tolerance is right for *display* and wrong for *decisions*. A read whose
answer gates a branch/commit decision has a checked sibling that raises rather
than guessing -- `status_lines` over `changed_files`, `require_branch_exists`
over `branch_exists`, `require_default_branch` over `resolve_default_branch`.
Guessing here is not harmless: a failed `git status` read as "tree is clean"
commits on top of the user's uncommitted work, and a failed `branch_exists`
read as "no such branch" cuts a second branch for a source that already had
one. Use the tolerant form for anything that only renders.
"""

import contextlib
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# `git commit` can block indefinitely on an interactive signing prompt (SSH
# signing via a hardware key or 1Password pops a GUI approval a human has to
# click), so it gets its own generous budget rather than the default below.
# Deliberately not TTY-gated: `sys.stdin.isatty()` is False under
# `wakil mcp serve` and under pytest's CliRunner, which is exactly where a
# GUI signing prompt can still appear -- gating on it would shorten the
# timeout in the case that needs it most. The knowing tradeoff: under
# `mcp serve` a hung commit now blocks a tool call for up to ten minutes,
# likely past the client's own patience. Lower WAKIL_GIT_COMMIT_TIMEOUT for
# that deployment if it matters more than surviving a slow signing prompt.
#
# Known gap: `subprocess.run` SIGKILLs only the direct child, so a signing
# helper or hook git spawned survives the timeout, reparented to init. Running
# the child in its own process group so the group could be killed was tried
# and reverted: `TimeoutExpired` carries no pid to kill (so the cleanup never
# fired), and `setsid()` drops the controlling terminal, which breaks
# `pinentry-tty` and ssh passphrase prompts on *every* checked git write --
# strictly worse than the orphan it was meant to reap.
COMMIT_TIMEOUT_SECONDS = 600
_COMMIT_TIMEOUT_ENV = "WAKIL_GIT_COMMIT_TIMEOUT"


class GitError(RuntimeError):
    pass


class GitTimeout(GitError):
    """We killed the git process because it exceeded its deadline.

    Typed rather than sniffed from the message: `_run_git_checked` embeds the
    full argv in its failure text, and that argv includes the commit message
    — which is model-generated from ingested content. A transcript titled
    "the day our API timed out" was enough to misclassify an ordinary commit
    failure as a timeout and route it into stale-lock cleanup, deleting an
    `index.lock` held by a live process.
    """


def commit_timeout() -> int:
    """Seconds to allow `git commit`. Override with WAKIL_GIT_COMMIT_TIMEOUT.

    An unusable value warns rather than silently reverting: a typo'd
    `WAKIL_GIT_COMMIT_TIMEOUT=60s` behaving as 600 is the kind of thing nobody
    notices until a commit dies at the wrong moment.
    """
    raw = os.environ.get(_COMMIT_TIMEOUT_ENV)
    if not raw:
        return COMMIT_TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value > 0:
        return value
    # Plain stderr rather than the Rich console the CLI uses: this module is
    # also imported under `wakil mcp serve`, where anything on stdout would
    # corrupt the JSON-RPC stream.
    print(
        f"warning: ignoring {_COMMIT_TIMEOUT_ENV}={raw!r} (not a positive number of "
        f"seconds); using {COMMIT_TIMEOUT_SECONDS}.",
        file=sys.stderr,
    )
    return COMMIT_TIMEOUT_SECONDS


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


def _run_git_status(root: Path, *args: str, timeout: int = 60) -> tuple[int, str]:
    """Exit code plus output, for probes where a non-zero code is a legitimate
    answer ("no such ref") rather than a failure. Anything git can't run at
    all still raises -- that is not an answer."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"git {args[0]} failed: {exc}") from exc
    return result.returncode, (result.stdout or result.stderr).strip()


def _run_git_checked(root: Path, *args: str, timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitTimeout(f"git {args[0]} timed out after {timeout} seconds.") from exc
    except OSError as exc:
        raise GitError(f"git {args[0]} failed: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise GitError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def changed_files(root: Path) -> list[str]:
    """Porcelain status lines, e.g. ' M notes/a.md' or '?? drafts/new.md'.

    Display-only: an empty list can mean "clean" *or* "couldn't tell". Use
    `status_lines` when the answer gates a write."""
    porcelain = _run_git(root, "status", "--porcelain")
    return porcelain.splitlines() if porcelain else []


def status_lines(root: Path) -> list[str]:
    """`changed_files`, but a failed read raises instead of reporting a clean
    tree -- which would let wakil branch and commit on top of the user's
    uncommitted work."""
    return _run_git_checked(root, "status", "--porcelain").splitlines()


def current_branch(root: Path) -> str:
    """The branch HEAD actually points at, right now. Raises on a detached
    HEAD or an unreadable repo rather than returning a plausible guess --
    every caller uses this to decide where a commit is about to land."""
    name = _run_git_checked(root, "rev-parse", "--abbrev-ref", "HEAD")
    if not name or name == "HEAD":
        raise GitError("HEAD is detached; wakil needs a named branch to commit onto.")
    return name


def create_branch(root: Path, name: str) -> None:
    _run_git_checked(root, "switch", "-c", name)


def create_branch_from(root: Path, name: str, base: str) -> None:
    """Create and switch to `name`, branching from `base` (a local branch
    name, e.g. the repo's default branch) rather than whatever is currently
    checked out.

    `base` is required. It used to accept None and fall back to branching
    from current HEAD, which meant an unreadable `origin/HEAD` symref quietly
    cut a fresh "ingest" branch off whatever unrelated branch happened to be
    checked out -- see `require_default_branch`."""
    remote_ref = f"origin/{base}"
    _run_git(root, "fetch", "origin", base)  # best-effort refresh; ignore failure
    if _run_git(root, "rev-parse", "--verify", remote_ref) is not None:
        _run_git_checked(root, "switch", "-c", name, remote_ref)
    else:
        _run_git_checked(root, "switch", "-c", name, base)


def branch_exists(root: Path, name: str) -> bool:
    """Display-only: False can mean "no such branch" *or* "couldn't tell"."""
    return _run_git(root, "rev-parse", "--verify", f"refs/heads/{name}") is not None


def require_branch_exists(root: Path, name: str) -> bool:
    """`branch_exists`, but a git failure raises instead of answering False.

    A false "no" here is expensive: `_resume_source_branch` falls through to
    cutting a *second* branch for a source that already had one, and the DB's
    recorded branch then permanently disagrees with the branch in use."""
    code, detail = _run_git_status(root, "show-ref", "--verify", "--quiet", f"refs/heads/{name}")
    if code == 0:
        return True
    if code == 1:
        return False
    raise GitError(f"git show-ref failed while checking for branch {name!r}: {detail}")


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


def require_default_branch(root: Path) -> str:
    """`resolve_default_branch`, but unresolvable is an error rather than a
    licence to branch from whatever is checked out."""
    name = resolve_default_branch(root)
    if not name:
        raise GitError(
            "Could not determine the repository's default branch (no origin/HEAD symref "
            "and no local main/master). Set one with "
            "`git remote set-head origin --auto`, or use --local."
        )
    return name


def stage_and_commit(root: Path, paths: list[str], message: str) -> str:
    """Stage exactly `paths`, commit, and return the commit sha.

    The commit gets `commit_timeout()` rather than the default -- it is the
    one git call in wakil that can legitimately sit waiting on a human.
    """
    _run_git_checked(root, "add", "--", *paths)
    try:
        _run_git_checked(root, "commit", "-m", message, "--", *paths, timeout=commit_timeout())
    except GitTimeout as exc:
        raise GitTimeout(_recover_from_killed_commit(root, paths, message, exc)) from exc
    return _run_git_checked(root, "rev-parse", "HEAD")


def _recover_from_killed_commit(
    root: Path, paths: list[str], message: str, exc: GitError
) -> str:
    """Clean up after a `git commit` we killed, and say how to finish it.

    `subprocess.run` SIGKILLs the child on timeout, and git holds
    `.git/index.lock` across the whole commit -- including the signing prompt
    and any pre-commit hook. So the killed process leaves the lock behind, and
    then *every* subsequent git write in the repo fails ("Unable to create
    '.git/index.lock': File exists") until someone works out they have to
    delete a file. Recovering from a 10-minute timeout was strictly worse than
    the 60-second failure it replaced.

    Removing the lock here is a stale-lock cleanup rather than a race:
    `subprocess.run` has already reaped the child by the time it raises, and a
    lock held by *another* process would have made this commit fail instantly
    rather than time out.

    The message is written to `.git/COMMIT_EDITMSG` so the recovery command
    actually restores it. A plain `git commit` does not: `-m` messages only
    reach `COMMIT_EDITMSG` at a point the kill may well have pre-empted (a
    slow pre-commit hook runs before it), so the editor can open empty and
    abort.
    """
    git_dir = _git_dir(root)
    cleaned = []
    saved_message: Path | None = None
    if git_dir is not None:
        for lock in [git_dir / "index.lock", *git_dir.glob("next-index-*.lock")]:
            with contextlib.suppress(OSError):
                lock.unlink()
                cleaned.append(lock.name)
        editmsg = git_dir / "COMMIT_EDITMSG"
        with contextlib.suppress(OSError):
            editmsg.write_text(message, encoding="utf-8")
            saved_message = editmsg

    lines = [
        str(exc),
        "If it was waiting on an interactive signing prompt, raise "
        f"{_COMMIT_TIMEOUT_ENV} (seconds) and try again.",
    ]
    if cleaned:
        lines.append(f"Removed the stale {', '.join(cleaned)} the killed process left behind.")
    # Keep the pathspec. The commit that timed out was scoped to wakil's own
    # files; an unscoped recovery would commit everything in the index,
    # sweeping the user's unrelated staged work into a wakil commit.
    pathspec = " ".join(shlex.quote(path) for path in paths)
    lines.append("Your changes are still staged. Finish the commit by hand with:")
    if saved_message is not None:
        # Absolute path rather than `.git/COMMIT_EDITMSG`: in a linked
        # worktree `<root>/.git` is a *file*, so the relative form fails with
        # "could not read log file: Not a directory".
        lines.append(f"    git -C {root} commit -F {saved_message} -- {pathspec}")
    else:
        # Nothing was saved, so `-F` would commit these files under whatever
        # stale message a previous commit happened to leave behind.
        subject = message.splitlines()[0] if message else ""
        lines.append(f"    git -C {root} commit -m {shlex.quote(subject)} -- {pathspec}")
        if "\n" in message:
            lines.append("    (subject only — the message body could not be preserved)")
    return "\n".join(lines)


def _git_dir(root: Path) -> Path | None:
    """This checkout's own git directory -- not the common dir, since
    `index.lock` is per worktree."""
    path = _run_git(root, "rev-parse", "--absolute-git-dir")
    return Path(path) if path else None


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
