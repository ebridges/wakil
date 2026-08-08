"""An advisory lock around the sequence that owns the git working tree.

`wakil ingest`/`wakil enrich` do "checkout a branch → write files → commit →
return", which takes seconds to minutes (a model call and a human confirm sit
in the middle). Nothing stopped a second wakil process from doing the same
thing in the same checkout at the same time, so their checkouts and commits
interleaved: uncommitted edits were overwritten, HEAD moved under a running
command, and the same source got captured twice on two branches (issue #182).

The second process is not always another human's session. An orphaned
`wakil mcp serve` left over from an earlier session drives git on its own;
killing those was what stopped the interference for the reporter.

Why `fcntl.flock` rather than an `O_EXCL` PID file
--------------------------------------------------
The kernel releases a `flock` when the holding process exits or the file
descriptor closes — including on `SIGKILL` and on a crash. So there is no
stale-lock problem to solve: no TTL to tune, no PID-liveness probe, no reaper,
and no way to leave a workspace permanently wedged by killing wakil at the
wrong moment. The JSON written into the lock file is *only* for the error
message and may be stale; it is never consulted to decide whether the lock is
held. Please don't "improve" this into a PID file.

Why the key is `root_path`, not `state_root`
--------------------------------------------
The thing being protected is a git index and a working tree, and every linked
`git worktree` has its own. Two worktrees of one repository can safely run
wakil at once, and keying on `state_root` (which they deliberately share, so
they resolve to one `Workspace` row) would serialize them for no reason. See
the "Git worktrees fix the ingest lock race" entry in docs/TROUBLESHOOTING.md.

`git_lock` is **not reentrant**. `flock` is per open file description, not
per process, so nesting one `git_lock()` inside another — or running two
write tool calls concurrently in one `wakil mcp serve` process, which the MCP
SDK dispatches on worker threads — denies the inner acquisition. Take the
lock once, at the command/tool boundary.

`fcntl` is POSIX-only, so this module is too. wakil is a local-first CLI for
macOS/Linux and has no Windows support to preserve, so there is no shim here —
adding one would be speculative complexity for a platform nothing else in the
project targets.
"""

import contextlib
import hashlib
import json
import os
import sys
import time
from collections.abc import Iterator
from fcntl import LOCK_EX, LOCK_NB, LOCK_UN, flock
from pathlib import Path
from typing import TextIO

from wakil.config.settings import WorkspaceConfig

_LOCK_TIMEOUT_ENV = "WAKIL_GIT_LOCK_TIMEOUT"


class WorkspaceBusyError(RuntimeError):
    """Another wakil process holds this checkout's git lock."""


def _lock_path(config: WorkspaceConfig) -> Path:
    # Resolve here rather than trusting the caller: two spellings of one
    # checkout would otherwise hash to two lock files and silently provide no
    # mutual exclusion at all.
    digest = hashlib.sha256(str(config.root_path.resolve()).encode()).hexdigest()[:12]
    return config.wakil_dir / "locks" / f"git-{digest}.lock"


def _holder_info(path: Path) -> dict:
    """Whatever the lock file says about its holder. May be stale, and is
    never consulted to decide whether the lock is held — only to describe it.
    Never raises."""
    with contextlib.suppress(OSError, ValueError, json.JSONDecodeError):
        info = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(info, dict):
            return info
    return {}


def _describe_holder(path: Path) -> str:
    info = _holder_info(path)
    pid, argv = info.get("pid"), info.get("argv")
    if pid and argv:
        return f" (pid {pid}: {argv})"
    if pid:
        return f" (pid {pid})"
    return ""


@contextlib.contextmanager
def git_lock(config: WorkspaceConfig) -> Iterator[None]:
    """Hold this checkout's git lock for the duration of the block.

    Fails fast by default. Set WAKIL_GIT_LOCK_TIMEOUT to a number of seconds
    to wait for the holder to finish instead.
    """
    path = _lock_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        _acquire(handle, path, config)
        try:
            yield
        finally:
            # Truncating first means a released lock file doesn't sit around
            # advertising a pid that no longer holds anything.
            with contextlib.suppress(OSError):
                handle.seek(0)
                handle.truncate()
            with contextlib.suppress(OSError):
                flock(handle.fileno(), LOCK_UN)
    finally:
        handle.close()


def _acquire(handle: TextIO, path: Path, config: WorkspaceConfig) -> None:
    try:
        flock(handle.fileno(), LOCK_EX | LOCK_NB)
    except OSError as exc:
        timeout, ignored = _wait_seconds()
        if timeout <= 0:
            raise WorkspaceBusyError(_busy_message(path, config, ignored)) from exc
        _acquire_blocking(handle, path, config, timeout)

    handle.seek(0)
    handle.truncate()
    handle.write(
        json.dumps(
            {
                "pid": os.getpid(),
                "argv": " ".join(sys.argv[:3]),
                "root": str(config.root_path),
            }
        )
    )
    handle.flush()


def _acquire_blocking(
    handle: TextIO, path: Path, config: WorkspaceConfig, timeout: float
) -> None:
    """Poll rather than block indefinitely, so the wait is bounded and
    Ctrl-C still works."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            flock(handle.fileno(), LOCK_EX | LOCK_NB)
            return
        except OSError as exc:
            if time.monotonic() >= deadline:
                raise WorkspaceBusyError(
                    f"Timed out after {timeout:g}s waiting for another wakil process to "
                    f"release {config.root_path}{_describe_holder(path)}."
                ) from exc
            time.sleep(0.25)


def _busy_message(path: Path, config: WorkspaceConfig, ignored_timeout: str | None) -> str:
    holder = _holder_info(path)
    if holder.get("pid") == os.getpid():
        # Same process: the MCP SDK dispatches sync tools on worker threads,
        # and flock is per open file description, so a concurrent tool call in
        # this very server denies us. Pointing the operator at "a leftover
        # `wakil mcp serve`" would accuse the server they are talking to.
        return (
            "Another wakil operation in this same process is still holding "
            f"{config.root_path}. Retry once it returns — wakil takes this lock once "
            "per command, and it is not reentrant."
        )
    lines = [
        f"Another wakil process is using {config.root_path}{_describe_holder(path)}.",
        "Wait for it to finish, run this one against a separate `git worktree`, or set "
        "WAKIL_GIT_LOCK_TIMEOUT=<seconds> to wait. If nothing should be running, check "
        "for a leftover `wakil mcp serve` (`ps ax | grep 'wakil.*mcp serve'`).",
    ]
    if ignored_timeout is not None:
        lines.append(
            f"(Ignoring WAKIL_GIT_LOCK_TIMEOUT={ignored_timeout!r}: not a number of seconds, "
            "so this failed fast instead of waiting.)"
        )
    return "\n".join(lines)


def _wait_seconds() -> tuple[float, str | None]:
    """Seconds to wait, plus the raw value if it was set but unusable.

    An unparseable value used to be silently discarded, so a user who wrote
    `WAKIL_GIT_LOCK_TIMEOUT=30s` got an immediate failure with nothing
    indicating their opt-in had been thrown away."""
    raw = os.environ.get(_LOCK_TIMEOUT_ENV)
    if not raw:
        return 0.0, None
    try:
        return max(0.0, float(raw)), None
    except ValueError:
        return 0.0, raw
