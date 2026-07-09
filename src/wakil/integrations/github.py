"""Optional GitHub integration via the `gh` CLI.

Kept deliberately thin: wakil shells out to `gh` rather than speaking the
GitHub API, per the build plan. Everything degrades cleanly when `gh` is not
installed or not authenticated.
"""

import shutil
import subprocess
from pathlib import Path


class GhError(RuntimeError):
    pass


def gh_available() -> bool:
    return shutil.which("gh") is not None


def create_pull_request(root: Path, title: str, body: str) -> str:
    """Create a PR for the current branch; returns the PR URL."""
    try:
        result = subprocess.run(
            ["gh", "pr", "create", "--title", title, "--body", body],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=root,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GhError(f"gh pr create failed: {exc}") from exc
    if result.returncode != 0:
        raise GhError(f"gh pr create failed: {(result.stderr or result.stdout).strip()}")
    # gh prints the PR URL as the last line of stdout.
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return lines[-1].strip() if lines else ""
