"""Git-native knowledge-base changes: ingest branches, commits, PRs.

Commit messages follow the wakil conventions from the build plan
(`📥 wakil source:`, `📝 wakil note:`, ...) — `commit_message()` prefixes
each kind with its emoji (`COMMIT_EMOJI`) so every wakil-generated commit,
automatic or hand-constructed, carries it consistently. Only files wakil
itself wrote are ever staged, and branching requires a clean tree so
wakil's changes never mix with the user's uncommitted work.

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
    find_pull_request,
    gh_available,
    mark_pull_request_ready,
)
from wakil.storage.schema import GitChange, IngestRun, Source, Workspace

COMMIT_PREFIXES = ("ingest", "note", "link", "memory", "dream", "source", "chore")
PHASES = ("capture", "enrichment")

COMMIT_EMOJI = {
    "source": "📥",
    "ingest": "🧠",
    "note": "📝",
    "link": "🔗",
    "chore": "🔧",
    "memory": "💾",
    "dream": "💭",
}


class GitServiceError(RuntimeError):
    pass


class BranchDriftError(GitServiceError):
    """HEAD is not the branch this landing resolved.

    Distinct from other landing failures because the response has to be
    different: an ordinary push/`gh` failure should return the tree to the
    default branch, but drift means another process owns this working tree,
    and switching under it is the clobber we are trying to prevent (#182).
    """


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
    `land_ingestion` entirely (the `--local` escape hatch).

    Note there is no `original_branch`. It used to hold whatever branch the
    shell happened to be on when `prepare_landing` ran, and `land_ingestion`
    switched back to it at the end — minutes later for the CLI, and across an
    unbounded gap for MCP's prepare/apply split. That made `wakil enrich`
    "return to" a stale, unrelated branch from an earlier command (#181).
    `ensure_clean_for_branch` already guarantees a clean tree at prepare
    time, so the incidental branch carries no information wakil needs; the
    landing now returns to the repo's default branch instead.
    """

    branch: str | None
    local: bool


def commit_message(kind: str, description: str) -> str:
    if kind not in COMMIT_PREFIXES:
        raise GitServiceError(f"unknown commit kind: {kind}")
    return f"{COMMIT_EMOJI[kind]} wakil {kind}: {description}"


def ingest_branch_name(root, title: str) -> str:
    date = datetime.now(UTC).date().isoformat()
    base = f"wakil/ingest/{date}-{slugify(title, max_length=40)}"
    name = base
    counter = 1
    while git.require_branch_exists(root, name):
        name = f"{base}-{counter}"
        counter += 1
    return name


def ensure_clean_for_branch(config: WorkspaceConfig) -> None:
    """Refuse to start or resume a wakil branch on top of uncommitted user
    changes."""
    info = git.inspect_git(config.root_path)
    if not info.is_repo:
        raise GitServiceError("Workspace is not a git repository; use --local to write locally.")
    try:
        changed = git.status_lines(config.root_path)
    except git.GitError as exc:
        raise GitServiceError(f"Could not read the working tree's status: {exc}") from exc
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
        return LandingContext(branch=None, local=True)
    ensure_clean_for_branch(config)
    state = _load_source_git_state(config, source_id) if source_id is not None else None
    if state is not None and state.git_branch:
        branch = _resume_source_branch(config, state)
    else:
        # Prefer the source's own recorded title (set at capture time) over
        # the caller-supplied one, which for `wakil enrich` is just a
        # placeholder until the enrichment proposal exists.
        branch_title = (state.title if state is not None else None) or title
        branch = _create_fresh_branch(config, branch_title, follow_up=state is not None)
    if source_id is not None:
        # Record the branch now, while it's still true. Waiting until after
        # the commit meant a source whose capture failed mid-landing kept a
        # NULL git_branch, and the next run resolved a different branch with
        # nothing to reconcile against.
        _remember_source_branch(config, source_id, branch)
    return LandingContext(branch=branch, local=False)


def abandon_landing(config: WorkspaceConfig, context: LandingContext) -> None:
    """Nothing was written for this landing context (e.g. enrichment
    proposed no files) — return to the default branch. No-op for a local
    context."""
    if context.local:
        return
    _return_to_default(config)


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
    # prepare_landing only ever leaves branch unset when local=True.
    assert context.branch is not None
    if not files:
        raise GitServiceError("Nothing to commit: no files were written.")
    if phase not in PHASES:
        raise GitServiceError(f"unknown phase: {phase!r}")

    message = commit_message(kind, f"add {title}")
    if summary:
        message += f"\n\n{summary}"

    landed_on = _assert_on_branch(
        config,
        context.branch,
        what="commit",
        state="The written files are still on disk; nothing was committed.",
    )
    # Record before committing, not after, and record the *observed* branch.
    # Two reasons, both about the next run: the capture flow can't record at
    # prepare time (the Source row doesn't exist yet, so `prepare_landing`
    # gets source_id=None), and a landing that fails mid-commit otherwise
    # leaves git_branch NULL -- so the next run cuts a fresh branch off the
    # default with nothing to reconcile against (#180), and
    # `_load_source_text` then fails with "Could not read raw capture",
    # because the capture only exists on the branch that was abandoned. That
    # is also what makes #171's finish-by-hand recovery actually resumable.
    _remember_source_branch(config, source_id, landed_on)
    try:
        sha = git.stage_and_commit(config.root_path, files, message)
    except git.GitError as exc:
        # Deliberately stay put. `git add` may have succeeded and only the
        # commit failed (e.g. a signing prompt timed out), so switching away
        # now would strand staged work on a branch the caller is no longer on
        # and force them to reconstruct the commit by hand -- the recovery
        # cost reported in issue #171.
        raise GitServiceError(str(exc)) from exc

    # The pre-commit assertion only narrows the window; HEAD can still move
    # between it and the commit itself. Verifying the sha actually landed on
    # the branch we claim is what makes `landed_on` an observation rather than
    # a label -- which is the whole thesis of this change (#181).
    _assert_commit_landed(config, landed_on, sha)

    # Record the commit now, not after the PR. It is durable in git from this
    # point; a push/`gh` failure used to leave a real commit with no
    # `git_changes` row at all, and re-running could not recover because
    # `stage_and_commit` fails outright when there is nothing left to commit.
    _record_change(config, files, sha, landed_on, None, title, ingest_run_id, kind)

    try:
        pr_url = _land_pr(config, source_id, landed_on, title, summary, files, kind, phase)
    except BranchDriftError:
        # Deliberately no _return_to_default here: the whole point of
        # detecting drift is that someone else owns this tree.
        raise
    except GitServiceError:
        _return_to_default(config)
        raise

    if pr_url:
        _attach_pr_to_change(config, sha, pr_url, ingest_run_id)
    returned_to = _return_to_default(config)
    return CommitOutcome(
        branch=landed_on,
        commit_sha=sha,
        message=message,
        pr_url=pr_url,
        returned_to=returned_to,
    )


def assert_landing_intact(config: WorkspaceConfig, context: LandingContext) -> None:
    """Check the tree is still ours *before* writing to it.

    `land_ingestion` asserts HEAD before the commit, but by then
    `apply_capture`/`apply_enrichment` have already written — and
    `_apply_entity_updates` rewrites *existing* notes. Under drift that meant
    wakil edited the user's notes into another process's working tree and only
    then refused to commit: the refusal was correct and far too late, since
    the clobber `BranchDriftError` exists to prevent had already happened on
    disk (working agreement item 12).

    No-op for a local context, which owns no branch.
    """
    if context.local or context.branch is None:
        return
    _assert_on_branch(
        config,
        context.branch,
        what="write to the knowledge base",
        state="Nothing has been written yet.",
    )


def _assert_on_branch(config: WorkspaceConfig, expected: str, *, what: str, state: str) -> str:
    """HEAD must actually be `expected` before wakil writes history.

    `land_ingestion` used to commit to whatever HEAD happened to be and then
    *print* `context.branch` — so "Committed abc123 on wakil/ingest/…" was a
    label, not an observation, and enrichment output could land silently on
    `main` (#181).

    On a mismatch this raises `BranchDriftError` rather than switching back.
    HEAD moving mid-landing means another process is in this working tree
    (#182), and switching under it would clobber its work. The advisory lock
    is what prevents the drift; this is what makes it loud.

    `state` describes what is already durable at this call site. It is a
    parameter rather than a fixed sentence because the same assertion runs
    before the commit and again before the push, and telling a user "nothing
    was committed" after the commit succeeded is the same
    label-instead-of-observation defect this function exists to fix.
    """
    try:
        observed = git.current_branch(config.root_path)
    except git.GitError as exc:
        raise GitServiceError(f"Refusing to {what}: could not read HEAD ({exc}).") from exc
    if observed != expected:
        raise BranchDriftError(
            f"Refusing to {what}: expected to be on {expected!r}, but HEAD is on "
            f"{observed!r}. Something moved this working tree since the branch was "
            f"resolved — most likely another wakil process (check with "
            f"`ps ax | grep 'wakil'`). {state}"
        )
    return observed


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
    # Only called when state.git_branch is truthy (see prepare_landing's guard).
    assert state.git_branch is not None
    name = state.git_branch
    try:
        exists = git.require_branch_exists(root, name)
    except git.GitError as exc:
        raise GitServiceError(
            f"Could not check whether branch {name!r} exists ({exc}). Refusing to guess: "
            f"treating this as 'missing' would cut a second branch for source {state.id}."
        ) from exc
    if exists:
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
    #
    # Forget the recorded PR at the same time. It belongs to the branch we are
    # abandoning, and `_land_pr` trusts a truthy `git_pr_url` unconditionally:
    # keeping it would flip that (probably merged) PR to ready, comment on it,
    # and record it against a commit it does not contain -- while the
    # follow-up branch silently got no PR at all, losing the reviewable-diff
    # boundary ADR 0003 exists to provide.
    _forget_source_pr(config, state.id)
    return _create_fresh_branch(config, state.title or f"source-{state.id}", follow_up=True)


def _create_fresh_branch(config: WorkspaceConfig, title: str, follow_up: bool = False) -> str:
    root = config.root_path
    try:
        base = git.require_default_branch(root)
    except git.GitError as exc:
        raise GitServiceError(str(exc)) from exc
    branch_title = f"{title} revision" if follow_up else title
    try:
        name = ingest_branch_name(root, branch_title)
        git.create_branch_from(root, name, base)
    except git.GitError as exc:
        raise GitServiceError(str(exc)) from exc
    return name


def _assert_commit_landed(config: WorkspaceConfig, branch: str, sha: str) -> None:
    """The commit must actually be on `branch`.

    Closes the window between the pre-commit HEAD assertion and the commit
    itself: if HEAD moved in between, the commit landed elsewhere and every
    downstream use of the branch name -- the `git_changes` row,
    `CommitOutcome.branch`, the CLI's "Committed <sha> on <branch>" -- would
    be a label again, which is verbatim the #181 report through a smaller
    window.
    """
    try:
        tip = git.rev_parse(config.root_path, branch)
    except git.GitError as exc:
        raise GitServiceError(
            f"Committed {sha[:10]} but could not verify which branch it landed on ({exc}). "
            f"Check `git branch --contains {sha[:10]}` before pushing."
        ) from exc
    if tip != sha:
        raise BranchDriftError(
            f"Commit {sha[:10]} did not land on {branch!r} — that branch is at "
            f"{tip[:10]}. Something moved this working tree during the commit, most "
            f"likely another wakil process. The commit is durable; find it with "
            f"`git branch --contains {sha[:10]}` and move it where it belongs."
        )


def _attach_pr_to_change(
    config: WorkspaceConfig, sha: str, pr_url: str, ingest_run_id: int | None
) -> None:
    """Fill in the PR URL on the `git_changes` row recorded at commit time."""
    with open_session(config) as session:
        change = session.scalar(select(GitChange).where(GitChange.commit_sha == sha))
        if change is not None:
            change.pr_url = pr_url
        if ingest_run_id is not None:
            run = session.get(IngestRun, ingest_run_id)
            if run is not None:
                run.created_pr_url = pr_url
        session.commit()


def _forget_source_pr(config: WorkspaceConfig, source_id: int) -> None:
    """Clear a recorded PR that no longer corresponds to the source's branch."""
    with open_session(config) as session:
        row = session.get(Source, source_id)
        if row is not None:
            row.git_pr_url = None
        session.commit()


def _remember_source_branch(config: WorkspaceConfig, source_id: int, branch: str) -> None:
    """Record the branch this source is landing on.

    Overwrites deliberately. It used to refuse when a value was already
    present, so once `_resume_source_branch` fell through to a fresh
    follow-up branch, the DB kept pointing at the dead one forever and every
    later run resolved differently (#180)."""
    with open_session(config) as session:
        row = session.get(Source, source_id)
        if row is not None:
            row.git_branch = branch
        session.commit()


def _remember_source_pr(config: WorkspaceConfig, source_id: int, pr_url: str) -> None:
    if not pr_url:
        # A falsy URL round-trips as "no PR yet" and opens a second one.
        return
    with open_session(config) as session:
        row = session.get(Source, source_id)
        if row is not None:
            row.git_pr_url = pr_url
        session.commit()


def _return_to_default(config: WorkspaceConfig) -> str | None:
    """Leave the working tree on the repo's default branch.

    Best-effort: a failure here leaves the session on the ingest branch,
    which is recoverable, whereas raising would fail a command whose work is
    already committed and pushed. Returns the branch actually ended up on, so
    the caller reports an observation rather than an intention."""
    root = config.root_path
    try:
        target = git.require_default_branch(root)
        if git.current_branch(root) != target:
            git.checkout(root, target)
        return git.current_branch(root)
    except git.GitError:
        pass
    try:
        return git.current_branch(root)  # report wherever we actually ended up
    except git.GitError:
        return None


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
    # `gh` infers the repo from cwd and (without --head) the branch from HEAD,
    # so a drifted HEAD would push/open against someone else's branch (#180).
    _assert_on_branch(
        config,
        branch,
        what="push and open a PR",
        state=(
            f"The commit itself succeeded and is on {branch!r} — it just has not been "
            "pushed and has no PR yet. Re-run once the other process has finished."
        ),
    )
    try:
        git.push_branch(config.root_path, branch)
    except git.GitError as exc:
        raise GitServiceError(str(exc)) from exc

    state = _load_source_git_state(config, source_id)
    existing_pr = state.git_pr_url if state else None
    if not existing_pr:
        # Ask GitHub, not just wakil's own DB. A PR can exist without being
        # recorded here (an interrupted earlier run, a hand-opened PR), and
        # discovering that at `gh pr create` time is a hard failure.
        try:
            existing_pr = find_pull_request(config.root_path, branch)
        except GhError as exc:
            raise GitServiceError(str(exc)) from exc
        if existing_pr:
            _remember_source_pr(config, source_id, existing_pr)
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
        base = git.require_default_branch(config.root_path)
    except git.GitError as exc:
        raise GitServiceError(str(exc)) from exc
    try:
        pr_url = create_pull_request(
            config.root_path,
            commit_message(kind, f"add {title}"),
            "\n".join(body_lines),
            head=branch,
            base=base,
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
            select(Workspace.id).where(Workspace.root_path == str(config.state_root))
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
