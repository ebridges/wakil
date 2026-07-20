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


def create_pull_request(root: Path, title: str, body: str, draft: bool = False) -> str:
    """Create a PR for the current branch; returns the PR URL."""
    args = ["gh", "pr", "create", "--title", title, "--body", body]
    if draft:
        args.append("--draft")
    try:
        result = subprocess.run(
            args,
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


def comment_on_pull_request(root: Path, pr_url: str, body: str) -> None:
    """Post a comment on an existing PR — used to record each phase (capture,
    enrichment) that lands on a source's branch after the PR already exists."""
    try:
        result = subprocess.run(
            ["gh", "pr", "comment", pr_url, "--body", body],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=root,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GhError(f"gh pr comment failed: {exc}") from exc
    if result.returncode != 0:
        raise GhError(f"gh pr comment failed: {(result.stderr or result.stdout).strip()}")


def mark_pull_request_ready(root: Path, pr_url: str) -> None:
    """Flip a draft PR to ready-for-review — used once enrichment lands on
    top of a capture-only draft PR."""
    try:
        result = subprocess.run(
            ["gh", "pr", "ready", pr_url],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=root,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GhError(f"gh pr ready failed: {exc}") from exc
    if result.returncode != 0:
        raise GhError(f"gh pr ready failed: {(result.stderr or result.stdout).strip()}")
