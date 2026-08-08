"""The advisory git lock (issue #182)."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from wakil.app.locking import WorkspaceBusyError, git_lock
from wakil.app.workspace_service import init_workspace
from wakil.config.settings import WorkspaceConfig


@pytest.fixture
def locked_kb(kb_path: Path) -> WorkspaceConfig:
    init_workspace(kb_path)
    return WorkspaceConfig.load(kb_path)


def _hold_lock_script(root: Path, ready: Path, release: Path, hard_exit: bool) -> str:
    """A separate *process*, because only a real child can be SIGKILLed to
    exercise the kernel-releases-the-lock path. (Note a *thread* would also
    contend -- `flock` is per open file description, not per process.)"""
    return textwrap.dedent(f"""
        import os, time, pathlib
        sys_path = {sys.path!r}
        import sys; sys.path[:0] = sys_path
        from wakil.app.locking import git_lock
        from wakil.config.settings import WorkspaceConfig
        config = WorkspaceConfig.load(pathlib.Path({str(root)!r}))
        with git_lock(config):
            pathlib.Path({str(ready)!r}).write_text("ready")
            while not pathlib.Path({str(release)!r}).exists():
                time.sleep(0.02)
            {"os._exit(0)" if hard_exit else "pass"}
    """)


def _spawn_holder(tmp_path: Path, root: Path, *, hard_exit: bool = False):
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    script = tmp_path / f"holder_{'kill' if hard_exit else 'clean'}.py"
    script.write_text(_hold_lock_script(root, ready, release, hard_exit), encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script)])
    for _ in range(500):
        if ready.exists():
            return proc, release
        if proc.poll() is not None:
            raise AssertionError(f"lock holder exited early with {proc.returncode}")
        import time

        time.sleep(0.02)
    proc.kill()
    raise AssertionError("lock holder never acquired the lock")


def test_lock_can_be_reacquired_after_clean_release(locked_kb):
    """Sequential, not reentrant -- `flock` is per open file description, so
    nesting two `git_lock()` calls in one process denies the inner one."""
    with git_lock(locked_kb):
        pass
    with git_lock(locked_kb):
        pass


def test_second_process_fails_fast_and_names_the_holder(locked_kb, tmp_path, monkeypatch):
    monkeypatch.delenv("WAKIL_GIT_LOCK_TIMEOUT", raising=False)
    proc, release = _spawn_holder(tmp_path, locked_kb.root_path)
    try:
        with pytest.raises(WorkspaceBusyError) as excinfo, git_lock(locked_kb):
            pytest.fail("acquired a lock another process is holding")
        message = str(excinfo.value)
        assert str(locked_kb.root_path) in message
        assert f"pid {proc.pid}" in message
        # The error has to be actionable -- the reporter's actual fix was
        # killing an orphaned `wakil mcp serve`.
        assert "mcp serve" in message
    finally:
        release.write_text("go")
        proc.wait(timeout=10)


def test_lock_releases_when_the_holder_is_killed(locked_kb, tmp_path, monkeypatch):
    """This is *the* argument for flock over an O_EXCL PID file: the kernel
    drops the lock when the holder dies, so there is no stale-lock state to
    detect, expire, or reap -- and no way to wedge a workspace permanently by
    killing wakil at the wrong moment."""
    monkeypatch.delenv("WAKIL_GIT_LOCK_TIMEOUT", raising=False)
    proc, release = _spawn_holder(tmp_path, locked_kb.root_path, hard_exit=True)
    release.write_text("go")
    proc.wait(timeout=10)

    # No cleanup call of any kind in between.
    with git_lock(locked_kb):
        pass


def test_lock_waits_when_a_timeout_is_configured(locked_kb, tmp_path, monkeypatch):
    monkeypatch.setenv("WAKIL_GIT_LOCK_TIMEOUT", "0.3")
    proc, release = _spawn_holder(tmp_path, locked_kb.root_path)
    try:
        with pytest.raises(WorkspaceBusyError, match="Timed out after"), git_lock(locked_kb):
            pytest.fail("acquired a lock another process is holding")
    finally:
        release.write_text("go")
        proc.wait(timeout=10)


def test_lock_file_lives_under_the_wakil_dir(locked_kb):
    with git_lock(locked_kb):
        locks = list((locked_kb.wakil_dir / "locks").glob("git-*.lock"))
    assert len(locks) == 1
    # .wakil/ is gitignored, so the lock never shows up as a working-tree change.
    assert locked_kb.wakil_dir in locks[0].parents


def test_lock_key_is_per_checkout_not_per_workspace(locked_kb, tmp_path):
    """Two linked worktrees share `state_root` (one Workspace row) but have
    independent git indexes, so they must not serialize against each other."""
    from wakil.app.locking import _lock_path

    other = WorkspaceConfig.load(locked_kb.root_path)
    other.root_path = tmp_path / "linked-worktree"
    assert _lock_path(other) != _lock_path(locked_kb)


def test_released_lock_file_does_not_advertise_a_stale_pid(locked_kb):
    with git_lock(locked_kb):
        path = next((locked_kb.wakil_dir / "locks").glob("git-*.lock"))
        assert str(os.getpid()) in path.read_text(encoding="utf-8")
    assert path.read_text(encoding="utf-8") == ""


# --- the wiring, not just the primitive -----------------------------------


def test_nesting_denies_itself_and_says_so(locked_kb, monkeypatch):
    """`flock` is per open file description, so a second acquisition in the
    same process is denied. The message must not blame 'a leftover
    wakil mcp serve' -- under `mcp serve` that is the server talking."""
    monkeypatch.delenv("WAKIL_GIT_LOCK_TIMEOUT", raising=False)
    with pytest.raises(WorkspaceBusyError) as excinfo, git_lock(locked_kb), git_lock(locked_kb):
        pytest.fail("git_lock is not reentrant")
    message = str(excinfo.value)
    assert "same process" in message
    assert "mcp serve" not in message


def test_an_unusable_timeout_value_is_reported_not_swallowed(locked_kb, tmp_path, monkeypatch):
    monkeypatch.setenv("WAKIL_GIT_LOCK_TIMEOUT", "30s")
    proc, release = _spawn_holder(tmp_path, locked_kb.root_path)
    try:
        with pytest.raises(WorkspaceBusyError) as excinfo, git_lock(locked_kb):
            pytest.fail("acquired a lock another process is holding")
        assert "30s" in str(excinfo.value)
    finally:
        release.write_text("go")
        proc.wait(timeout=10)


def test_lock_key_is_independent_of_how_the_path_was_spelled(locked_kb, tmp_path):
    """Two spellings of one checkout must not hash to two lock files -- that
    would silently provide no mutual exclusion at all."""
    from wakil.app.locking import _lock_path
    from wakil.config.settings import WorkspaceConfig

    unresolved = WorkspaceConfig.load(locked_kb.root_path)
    unresolved.root_path = Path(str(locked_kb.root_path) + "/./")
    assert _lock_path(unresolved) == _lock_path(locked_kb)
