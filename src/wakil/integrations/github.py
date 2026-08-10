"""Optional GitHub integration via the `gh` CLI.

Kept deliberately thin: wakil shells out to `gh` rather than speaking the
GitHub API, per the build plan. Everything degrades cleanly when `gh` is not
installed or not authenticated.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

_PR_URL_RE = re.compile(r"^https?://\S+$")


class GhError(RuntimeError):
    pass


def gh_available() -> bool:
    return shutil.which("gh") is not None


def create_pull_request(
    root: Path, title: str, body: str, *, head: str, base: str, draft: bool = False
) -> str:
    """Create a PR for `head` into `base`; returns the PR URL.

    `head`/`base` are explicit on purpose. Without them `gh` infers the head
    branch from whatever is checked out in `cwd`, so if HEAD had drifted
    since the branch was resolved, wakil opened (or collided with) a PR for a
    completely unrelated source -- issue #180's "a pull request for branch
    <someone else's branch> already exists".
    """
    args = ["gh", "pr", "create", "--head", head, "--base", base, "--title", title, "--body", body]
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
    # gh prints the PR URL as the last line of stdout. An empty stdout used to
    # yield "", which is falsy -- it was persisted as the source's PR URL and
    # then read back as "no PR yet", opening a second one on the next run.
    for line in reversed([line.strip() for line in result.stdout.splitlines() if line.strip()]):
        if _PR_URL_RE.match(line):
            return line
    raise GhError(f"gh pr create reported success but printed no PR URL: {result.stdout!r}")


def find_pull_request(root: Path, head: str) -> str | None:
    """URL of the open PR whose head branch is exactly `head`, or None.

    Matches on the branch name rather than trusting only wakil's own DB, so a
    PR that exists on GitHub but isn't recorded locally is adopted instead of
    causing a hard "already exists" failure at create time. A `gh` failure
    raises -- an auth error must not read as "there is no PR"."""
    try:
        result = subprocess.run(
            [
                "gh", "pr", "list",
                "--head", head,
                "--state", "open",
                "--json", "url",
                "--limit", "1",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=root,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GhError(f"gh pr list failed: {exc}") from exc
    if result.returncode != 0:
        raise GhError(f"gh pr list failed: {(result.stderr or result.stdout).strip()}")
    try:
        rows = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise GhError(f"gh pr list returned unparseable JSON: {result.stdout!r}") from exc
    if not rows:
        return None
    url = rows[0].get("url")
    return url if isinstance(url, str) and url else None


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
