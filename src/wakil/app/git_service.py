"""Git-native knowledge-base changes: ingest branches, commits, PRs.

Commit messages follow the wakil conventions from the build plan
(`wakil ingest:`, `wakil note:`, ...). Only files wakil itself wrote are
ever staged, and branching requires a clean tree so wakil's changes never
mix with the user's uncommitted work.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from wakil.app.ingest_service import slugify
from wakil.app.workspace_service import open_session
from wakil.config.settings import WorkspaceConfig
from wakil.integrations import git
from wakil.integrations.github import GhError, create_pull_request, gh_available
from wakil.storage.schema import GitChange, IngestRun, Workspace

COMMIT_PREFIXES = ("ingest", "note", "link", "memory", "dream", "source", "chore")


class GitServiceError(RuntimeError):
    pass


@dataclass
class CommitOutcome:
    branch: str | None  # branch created for this change, if any
    commit_sha: str
    message: str
    pr_url: str | None = None


def commit_message(kind: str, description: str) -> str:
    if kind not in COMMIT_PREFIXES:
        raise GitServiceError(f"unknown commit kind: {kind}")
    return f"wakil {kind}: {description}"


def ingest_branch_name(root, title: str) -> str:
    date = datetime.now(UTC).date().isoformat()
    base = f"wakil/ingest/{date}-{slugify(title, max_length=40)}"
    name = base
    counter = 1
    while git.branch_exists(root, name):
        name = f"{base}-{counter}"
        counter += 1
    return name


def ensure_clean_for_branch(config: WorkspaceConfig) -> None:
    """Refuse to start a wakil branch on top of uncommitted user changes."""
    info = git.inspect_git(config.root_path)
    if not info.is_repo:
        raise GitServiceError("Workspace is not a git repository; cannot use --branch/--commit.")
    changed = git.changed_files(config.root_path)
    if changed:
        listing = "\n".join(f"  {line}" for line in changed[:10])
        raise GitServiceError(
            f"Working tree has uncommitted changes; commit or stash them first:\n{listing}"
        )


def start_ingest_branch(config: WorkspaceConfig, title: str) -> str:
    ensure_clean_for_branch(config)
    name = ingest_branch_name(config.root_path, title)
    try:
        git.create_branch(config.root_path, name)
    except git.GitError as exc:
        raise GitServiceError(str(exc)) from exc
    return name


def commit_ingest(
    config: WorkspaceConfig,
    files: list[str],
    title: str,
    summary: str | None,
    ingest_run_id: int | None = None,
    branch: str | None = None,
    open_pr: bool = False,
) -> CommitOutcome:
    """Commit wakil-written files with the ingest convention; optionally PR."""
    if not files:
        raise GitServiceError("Nothing to commit: no files were written.")
    message = commit_message("ingest", f"add {title}")
    if summary:
        message += f"\n\n{summary}"
    try:
        sha = git.stage_and_commit(config.root_path, files, message)
    except git.GitError as exc:
        raise GitServiceError(str(exc)) from exc

    pr_url: str | None = None
    if open_pr:
        pr_url = _open_pr(config, branch, title, summary, files)

    _record_change(config, files, sha, branch, pr_url, title, ingest_run_id)
    return CommitOutcome(branch=branch, commit_sha=sha, message=message, pr_url=pr_url)


def _open_pr(
    config: WorkspaceConfig,
    branch: str | None,
    title: str,
    summary: str | None,
    files: list[str],
) -> str:
    if branch is None:
        raise GitServiceError("--pr requires --branch.")
    if not gh_available():
        raise GitServiceError("--pr requires the GitHub CLI (`gh`) on the PATH.")
    info = git.inspect_git(config.root_path)
    if not info.remote_url:
        raise GitServiceError("--pr requires an 'origin' remote.")
    body_lines = [summary or f"Ingest of {title}.", "", "Files:"]
    body_lines += [f"- `{path}`" for path in files]
    body_lines += ["", "Review checklist:", "- [ ] Raw capture looks right"]
    body_lines += ["- [ ] Proposed note placement and links are correct"]
    try:
        git.push_branch(config.root_path, branch)
        return create_pull_request(
            config.root_path, commit_message("ingest", f"add {title}"), "\n".join(body_lines)
        )
    except (git.GitError, GhError) as exc:
        raise GitServiceError(str(exc)) from exc


def _record_change(
    config: WorkspaceConfig,
    files: list[str],
    sha: str,
    branch: str | None,
    pr_url: str | None,
    title: str,
    ingest_run_id: int | None,
) -> None:
    with open_session(config) as session:
        workspace_id = session.scalar(
            select(Workspace.id).where(Workspace.root_path == str(config.root_path))
        )
        session.add(
            GitChange(
                workspace_id=workspace_id,
                operation="ingest-commit",
                branch_name=branch,
                commit_sha=sha,
                pr_url=pr_url,
                summary=f"ingest: {title}",
                metadata_json=json.dumps({"files": files}),
            )
        )
        if ingest_run_id is not None:
            run = session.get(IngestRun, ingest_run_id)
            if run is not None:
                run.created_branch = branch
                run.created_commit = sha
                run.created_pr_url = pr_url
        session.commit()
