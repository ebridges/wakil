"""Git-native knowledge-base changes: ingest branches, commits, PRs.

Commit messages follow the wakil conventions from the build plan
(`wakil ingest:`, `wakil note:`, ...). Only files wakil itself wrote are
ever staged, and branching requires a clean tree so wakil's changes never
mix with the user's uncommitted work.

Branch/commit/PR are on by default for `wakil ingest`/`wakil enrich`
(`--local` opts out) and are tracked per **source**, not per command
invocation: `prepare_landing` resolves (reusing, fetching, or freshly
creating) the one branch for a source, and `land_ingestion` commits onto
it and opens or updates the one PR for that source — draft after capture,
flipped to ready-for-review once enrichment lands. This is what lets a
later, separate `wakil enrich <source-id>` call (possibly a different
session or agent) land on the same branch/PR the capture step started,
instead of opening a second, disconnected PR.
"""

import contextlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from wakil.app.ingest_service import slugify
from wakil.app.workspace_service import open_session
from wakil.config.settings import WorkspaceConfig
from wakil.integrations import git
from wakil.integrations.github import (
    GhError,
    comment_on_pull_request,
    create_pull_request,
    gh_available,
    mark_pull_request_ready,
)
from wakil.storage.schema import GitChange, IngestRun, Source, Workspace

COMMIT_PREFIXES = ("ingest", "note", "link", "memory", "dream", "source", "chore")
PHASES = ("capture", "enrichment")


class GitServiceError(RuntimeError):
    pass


@dataclass
class CommitOutcome:
    branch: str | None  # branch this change landed on, if any
    commit_sha: str
    message: str
    pr_url: str | None = None
    returned_to: str | None = None  # branch we switched back to afterward, if any


@dataclass
class LandingContext:
    """What `prepare_landing` resolved, for `land_ingestion` to commit onto.

    `local=True` means no git operations at all — the caller should skip
    `land_ingestion` entirely (the `--local` escape hatch)."""

    branch: str | None
    original_branch: str | None
    local: bool


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
    """Refuse to start or resume a wakil branch on top of uncommitted user
    changes."""
    info = git.inspect_git(config.root_path)
    if not info.is_repo:
        raise GitServiceError("Workspace is not a git repository; use --local to write locally.")
    changed = git.changed_files(config.root_path)
    if changed:
        listing = "\n".join(f"  {line}" for line in changed[:10])
        raise GitServiceError(
            f"Working tree has uncommitted changes; commit or stash them first:\n{listing}"
        )


def commit_change(
    config: WorkspaceConfig,
    files: list[str],
    kind: str,
    description: str,
) -> CommitOutcome:
    """Commit wakil-written files on the current branch with a wakil
    convention message (e.g. `wakil chore: normalize person frontmatter`).
    Used by `wakil schema migrate --commit` — a plain local commit, not part
    of the per-source branch/PR lifecycle below."""
    if not files:
        raise GitServiceError("Nothing to commit: no files were written.")
    message = commit_message(kind, description)
    try:
        sha = git.stage_and_commit(config.root_path, files, message)
    except git.GitError as exc:
        raise GitServiceError(str(exc)) from exc
    _record_change(config, files, sha, None, None, description, None, kind)
    return CommitOutcome(branch=None, commit_sha=sha, message=message)


def prepare_landing(
    config: WorkspaceConfig,
    *,
    source_id: int | None,
    title: str,
    local: bool = False,
) -> LandingContext:
    """Call before writing any files. Resolves and switches onto the
    source's branch (reusing one already recorded on `Source.git_branch` if
    it still exists, fetching it if it only exists on origin, or starting
    fresh otherwise), leaving the working tree ready for `apply_capture`/
    `apply_enrichment` to write into. `local=True` skips all of this — no
    git operations, `land_ingestion` should not be called for this context.

    `source_id` is None only for a fresh capture, where the `Source` row
    doesn't exist yet; pass the known id from `wakil enrich` so an existing
    branch/PR can be resumed.
    """
    if local:
        return LandingContext(branch=None, original_branch=None, local=True)
    ensure_clean_for_branch(config)
    original_branch = git.inspect_git(config.root_path).branch
    state = _load_source_git_state(config, source_id) if source_id is not None else None
    if state is not None and state.git_branch:
        branch = _resume_source_branch(config, state)
    else:
        # Prefer the source's own recorded title (set at capture time) over
        # the caller-supplied one, which for `wakil enrich` is just a
        # placeholder until the enrichment proposal exists.
        branch_title = (state.title if state is not None else None) or title
        branch = _create_fresh_branch(config, branch_title, follow_up=state is not None)
    return LandingContext(branch=branch, original_branch=original_branch, local=False)


def abandon_landing(config: WorkspaceConfig, context: LandingContext) -> None:
    """Nothing was written for this landing context (e.g. enrichment
    proposed no files) — return to the original branch if we switched away
    from it. No-op for a local context."""
    if context.local:
        return
    _return_to_branch(config.root_path, context.original_branch)


def land_ingestion(
    config: WorkspaceConfig,
    context: LandingContext,
    *,
    source_id: int,
    files: list[str],
    title: str,
    summary: str | None,
    ingest_run_id: int | None,
    kind: str,
    phase: str,
) -> CommitOutcome:
    """Commit `files` onto `context.branch` and land the source's PR —
    creating it as a draft after capture, or flipping it to ready and
    posting a summary comment once enrichment lands on an already-open one.

    Not for a local context (`context.local` — the caller should skip
    calling this entirely and just report the files were written).
    """
    if context.local:
        raise GitServiceError("land_ingestion called with a local (no-git) context")
    if not files:
        raise GitServiceError("Nothing to commit: no files were written.")
    if phase not in PHASES:
        raise GitServiceError(f"unknown phase: {phase!r}")

    message = commit_message(kind, f"add {title}")
    if summary:
        message += f"\n\n{summary}"
    try:
        sha = git.stage_and_commit(config.root_path, files, message)
    except git.GitError as exc:
        _return_to_branch(config.root_path, context.original_branch)
        raise GitServiceError(str(exc)) from exc

    _remember_source_branch(config, source_id, context.branch)
    try:
        pr_url = _land_pr(config, source_id, context.branch, title, summary, files, kind, phase)
    except GitServiceError:
        _return_to_branch(config.root_path, context.original_branch)
        raise

    _record_change(config, files, sha, context.branch, pr_url, title, ingest_run_id, kind)
    _return_to_branch(config.root_path, context.original_branch)
    return CommitOutcome(
        branch=context.branch,
        commit_sha=sha,
        message=message,
        pr_url=pr_url,
        returned_to=context.original_branch,
    )


@dataclass
class _SourceGitState:
    id: int
    title: str | None
    git_branch: str | None
    git_pr_url: str | None


def _load_source_git_state(config: WorkspaceConfig, source_id: int) -> "_SourceGitState | None":
    with open_session(config) as session:
        row = session.get(Source, source_id)
        if row is None:
            return None
        return _SourceGitState(
            id=row.id, title=row.title, git_branch=row.git_branch, git_pr_url=row.git_pr_url
        )


def _resume_source_branch(config: WorkspaceConfig, state: "_SourceGitState") -> str:
    root = config.root_path
    name = state.git_branch
    if git.branch_exists(root, name):
        try:
            git.checkout(root, name)
        except git.GitError as exc:
            raise GitServiceError(str(exc)) from exc
        return name
    if git.fetch_branch(root, name):
        try:
            git.checkout_new_tracking(root, name)
        except git.GitError as exc:
            raise GitServiceError(str(exc)) from exc
        return name
    # Branch is gone entirely -- most likely its PR was merged and GitHub
    # deleted it. Don't error: start a follow-up branch/PR for this source.
    return _create_fresh_branch(config, state.title or f"source-{state.id}", follow_up=True)


def _create_fresh_branch(config: WorkspaceConfig, title: str, follow_up: bool = False) -> str:
    root = config.root_path
    base = git.resolve_default_branch(root)
    branch_title = f"{title} revision" if follow_up else title
    name = ingest_branch_name(root, branch_title)
    try:
        git.create_branch_from(root, name, base)
    except git.GitError as exc:
        raise GitServiceError(str(exc)) from exc
    return name


def _remember_source_branch(config: WorkspaceConfig, source_id: int, branch: str) -> None:
    with open_session(config) as session:
        row = session.get(Source, source_id)
        if row is not None and not row.git_branch:
            row.git_branch = branch
        session.commit()


def _remember_source_pr(config: WorkspaceConfig, source_id: int, pr_url: str) -> None:
    with open_session(config) as session:
        row = session.get(Source, source_id)
        if row is not None:
            row.git_pr_url = pr_url
        session.commit()


def _return_to_branch(root, original_branch: str | None) -> None:
    if not original_branch:
        return
    # Best-effort; leave on the ingest branch rather than crash post-commit.
    with contextlib.suppress(git.GitError):
        git.checkout(root, original_branch)


def _phase_comment(phase: str, title: str, summary: str | None, files: list[str]) -> str:
    header = "Enrichment landed." if phase == "enrichment" else "Capture landed."
    lines = [header]
    if summary:
        lines += ["", summary]
    lines += ["", "Files:"]
    lines += [f"- `{path}`" for path in files]
    return "\n".join(lines)


def _land_pr(
    config: WorkspaceConfig,
    source_id: int,
    branch: str,
    title: str,
    summary: str | None,
    files: list[str],
    kind: str,
    phase: str,
) -> str | None:
    """Push the branch and create-or-update the source's PR. Returns None
    (not an error) when `gh` or a remote isn't available — branch/commit
    still happen either way; the PR is opportunistic."""
    if not gh_available():
        return None
    info = git.inspect_git(config.root_path)
    if not info.remote_url:
        return None
    try:
        git.push_branch(config.root_path, branch)
    except git.GitError as exc:
        raise GitServiceError(str(exc)) from exc

    state = _load_source_git_state(config, source_id)
    existing_pr = state.git_pr_url if state else None
    if existing_pr:
        try:
            if phase == "enrichment":
                mark_pull_request_ready(config.root_path, existing_pr)
            comment_on_pull_request(
                config.root_path, existing_pr, _phase_comment(phase, title, summary, files)
            )
        except GhError as exc:
            raise GitServiceError(str(exc)) from exc
        return existing_pr

    body_lines = [summary or f"Ingest of {title}.", "", "Files:"]
    body_lines += [f"- `{path}`" for path in files]
    body_lines += ["", "Review checklist:", "- [ ] Raw capture looks right"]
    body_lines += ["- [ ] Proposed note placement and links are correct"]
    draft = phase == "capture"
    try:
        pr_url = create_pull_request(
            config.root_path,
            commit_message(kind, f"add {title}"),
            "\n".join(body_lines),
            draft=draft,
        )
    except GhError as exc:
        raise GitServiceError(str(exc)) from exc
    _remember_source_pr(config, source_id, pr_url)
    return pr_url


def _record_change(
    config: WorkspaceConfig,
    files: list[str],
    sha: str,
    branch: str | None,
    pr_url: str | None,
    title: str,
    ingest_run_id: int | None,
    kind: str = "ingest",
) -> None:
    with open_session(config) as session:
        workspace_id = session.scalar(
            select(Workspace.id).where(Workspace.root_path == str(config.root_path))
        )
        session.add(
            GitChange(
                workspace_id=workspace_id,
                operation=f"{kind}-commit",
                branch_name=branch,
                commit_sha=sha,
                pr_url=pr_url,
                summary=f"{kind}: {title}",
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
