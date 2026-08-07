"""MCP tool implementations: thin, plain-data wrappers around wakil's
`app/*_service.py` functions — no Rich/Typer, no interactive confirmation.

Registered as MCP tools by `server.py`; kept here as ordinary functions,
each taking `config`/`cache` explicitly, so they're directly unit-testable
without a running MCP server (docs/adr/0018).

Write tools (`ingest_prepare`/`ingest_apply`, `enrich_prepare`/`enrich_apply`)
mirror `_run_ingest`/`enrich` in `cli/main.py` exactly, minus Rich output and
`typer.confirm` — the prepare/apply split itself is what stands in for the
CLI's preview-then-confirm gate (docs/adr/0019).
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

from wakil.app.git_service import (
    BranchDriftError,
    CommitOutcome,
    GitServiceError,
    LandingContext,
    abandon_landing,
    assert_landing_intact,
    land_ingestion,
    prepare_landing,
)
from wakil.app.graph_service import Direction, TraversalError, traverse
from wakil.app.ingest_service import (
    IngestError,
    apply_capture,
    apply_enrichment,
    get_source,
    list_sources,
    prepare_capture,
    prepare_enrichment,
    validate_proposal,
)
from wakil.app.locking import WorkspaceBusyError, git_lock
from wakil.app.memory_service import MemoryError, get_memory, list_memories
from wakil.app.qmd_service import refresh_index
from wakil.app.query_service import run_query as _run_query
from wakil.app.search_service import get_workspace_id, search_workspace
from wakil.app.workspace_service import get_status, open_session
from wakil.config.settings import WorkspaceConfig
from wakil.integrations import git as git_integration
from wakil.integrations.qmd import qmd_list_collections
from wakil.llm.client import ModelError, resolve_client
from wakil.mcp.proposals import ProposalCache, ProposalNotFoundError
from wakil.skills.errors import SkillResolutionError
from wakil.skills.resolver import default_context, discover_skill_names, resolve_skill


class ToolError(RuntimeError):
    """Any wakil MCP tool failure. The message is shown to the calling
    client as-is — mirrors the CLI's `console.print(f"[red]...[/red]")`
    failure messages, minus the Rich markup."""


def _require_client():
    client = resolve_client()
    if client is None:
        raise ToolError(
            "No model provider configured. Set ANTHROPIC_API_KEY, or "
            "OPENAI_API_KEY + WAKIL_MODEL for an OpenAI-compatible endpoint."
        )
    return client


# --------------------------------------------------------------------------
# Read tools


def status(config: WorkspaceConfig) -> dict:
    ws = get_status(config.root_path)
    return {
        "name": ws.config.name,
        "root_path": str(ws.config.root_path),
        "note_count": ws.note_count,
        "source_count": ws.source_count,
        "memory_count": ws.memory_count,
        "git": {
            "is_repo": ws.git.is_repo,
            "branch": ws.git.branch,
            "is_dirty": ws.git.is_dirty,
            "remote_url": ws.git.remote_url,
        },
        "qmd": {"available": ws.qmd.available, "version": ws.qmd.version},
        "special_files": ws.special_files,
    }


def search(
    config: WorkspaceConfig, query: str, limit: int = 10, mode: str = "search"
) -> list[dict]:
    with open_session(config) as session:
        hits = search_workspace(session, config, query, limit=limit, mode=mode)
    return [
        {
            "kind": hit.kind,
            "ref": hit.ref,
            "title": hit.title,
            "snippet": hit.snippet,
            "engine": hit.engine,
            "score": hit.score,
            "state": hit.state,
        }
        for hit in hits
    ]


def query(
    config: WorkspaceConfig,
    question: str,
    limit: int = 10,
    mode: str = "search",
    include_casual: bool = False,
) -> dict:
    client = _require_client()
    try:
        result = _run_query(
            config, question, client, limit=limit, mode=mode, include_casual=include_casual
        )
    except ModelError as exc:
        raise ToolError(str(exc)) from exc
    return {
        "question": result.question,
        "answer": result.answer,
        "citations": [
            {"ref": c.ref, "kind": c.kind, "title": c.title} for c in result.contexts
        ],
    }


def _memory_dict(memory) -> dict:
    return {
        "id": memory.id,
        "memory_type": memory.memory_type,
        "content": memory.content,
        "summary": memory.summary,
        "state": memory.state,
        "stance": memory.stance,
        "confidence": memory.confidence,
        "importance": memory.importance,
        "source_id": memory.source_id,
        "note_id": memory.note_id,
        "event_date": memory.event_date.isoformat() if memory.event_date else None,
        "created_at": memory.created_at.isoformat() if memory.created_at else None,
    }


def memory_list(
    config: WorkspaceConfig,
    state: str | None = None,
    memory_type: str | None = None,
    stance: str | None = None,
    limit: int = 50,
) -> list[dict]:
    with open_session(config) as session:
        workspace_id = get_workspace_id(session, config)
        if workspace_id is None:
            return []
        try:
            memories = list_memories(
                session,
                workspace_id,
                state=state,
                memory_type=memory_type,
                stance=stance,
                limit=limit,
            )
        except MemoryError as exc:
            raise ToolError(str(exc)) from exc
        return [_memory_dict(m) for m in memories]


def memory_show(config: WorkspaceConfig, memory_id: int) -> dict:
    with open_session(config) as session:
        workspace_id = get_workspace_id(session, config)
        if workspace_id is None:
            raise ToolError("Workspace not initialized.")
        try:
            memory = get_memory(session, workspace_id, memory_id)
        except MemoryError as exc:
            raise ToolError(str(exc)) from exc
        return _memory_dict(memory)


def relationships(
    config: WorkspaceConfig,
    anchor_path: str,
    direction: Direction = "both",
    predicate: str | None = None,
    depth: int = 1,
) -> dict:
    with open_session(config) as session:
        workspace_id = get_workspace_id(session, config)
        if workspace_id is None:
            raise ToolError("Workspace not initialized.")
        try:
            result = traverse(
                session,
                workspace_id,
                anchor_path,
                direction=direction,
                predicate=predicate,
                depth=depth,
            )
        except TraversalError as exc:
            raise ToolError(str(exc)) from exc
    return {
        "anchor_path": result.anchor_path,
        "anchor_title": result.anchor_title,
        "direction": result.direction,
        "predicate": result.predicate,
        "depth": result.depth,
        "hits": [
            {
                "path": h.path,
                "title": h.title,
                "depth": h.depth,
                "via_predicate": h.via_predicate,
                "direction": h.direction,
            }
            for h in result.hits
        ],
    }


def _source_dict(source) -> dict:
    return {
        "id": source.id,
        "source_type": source.source_type,
        "title": source.title,
        "origin": source.origin,
        "status": source.status,
        "created_at": source.created_at.isoformat() if source.created_at else None,
        "updated_at": source.updated_at.isoformat() if source.updated_at else None,
        "git_branch": source.git_branch,
        "git_pr_url": source.git_pr_url,
    }


def sources_list(
    config: WorkspaceConfig, status: str | None = None, limit: int | None = 50
) -> list[dict]:
    rows = list_sources(config, status=status, limit=limit)
    return [_source_dict(row) for row in rows]


def sources_show(config: WorkspaceConfig, source_id: int) -> dict:
    try:
        row = get_source(config, source_id)
    except IngestError as exc:
        raise ToolError(str(exc)) from exc
    return _source_dict(row)


def git_summary(config: WorkspaceConfig) -> dict:
    root = config.root_path
    info = git_integration.inspect_git(root)
    if not info.is_repo:
        raise ToolError("This workspace is not a git repository.")
    return {
        "branch": info.branch,
        "is_dirty": info.is_dirty,
        "changed_files": git_integration.changed_files(root),
        "recent_commits": info.recent_commits,
        "wakil_branches": git_integration.wakil_branches(root),
    }


def git_history(config: WorkspaceConfig, path: str, limit: int = 10) -> list[str]:
    root = config.root_path
    if not git_integration.inspect_git(root).is_repo:
        raise ToolError("This workspace is not a git repository.")
    return git_integration.file_history(root, path, limit=limit)


def skills_list(config: WorkspaceConfig) -> list[dict]:
    context = default_context(config.root_path)
    rows = []
    for name in discover_skill_names(context):
        try:
            resolved = resolve_skill(name, context)
        except SkillResolutionError as exc:
            rows.append({"name": name, "source": "error", "detail": f"{exc.reason}: {exc}"})
        else:
            rows.append(
                {
                    "name": resolved.name,
                    "source": resolved.source,
                    "directory": str(resolved.directory),
                }
            )
    return rows


# --------------------------------------------------------------------------
# Write tools: ingest (prepare/apply)


@contextlib.contextmanager
def _git_lock_or_tool_error(config: WorkspaceConfig) -> Iterator[None]:
    """Serialize the git-owning part of a tool call, reporting a lost race as
    a ToolError the coordinating agent can act on."""
    try:
        with git_lock(config):
            yield
    except WorkspaceBusyError as exc:
        raise ToolError(str(exc)) from exc


def _land(
    config: WorkspaceConfig,
    landing: LandingContext,
    *,
    source_id: int,
    files: list[str],
    title: str,
    summary: str | None,
    ingest_run_id: int | None,
    kind: str,
    phase: str,
) -> CommitOutcome | None:
    if landing.local:
        return None
    try:
        return land_ingestion(
            config,
            landing,
            source_id=source_id,
            files=files,
            title=title,
            summary=summary,
            ingest_run_id=ingest_run_id,
            kind=kind,
            phase=phase,
        )
    except GitServiceError as exc:
        # Name the branch: a failed landing deliberately leaves HEAD on the
        # ingest branch (so staged work isn't stranded), and saying nothing
        # about that moves the caller's tree silently.
        if isinstance(exc, BranchDriftError):
            location = ""  # the exception already names both branches
        else:
            branch = git_integration.inspect_git(config.root_path).branch
            location = f" HEAD is on {branch}; changes may be staged there."
        raise ToolError(
            f"Landing failed: {exc} (written files are still on disk for manual review)."
            f"{location}"
        ) from exc


def _refresh_qmd(config: WorkspaceConfig) -> None:
    """Best-effort — the write itself already succeeded by the time this
    runs, so a refresh failure is silently skipped rather than failing the
    tool call (mirrors cli/main.py's `_refresh_qmd_index`)."""
    if not config.qmd_enabled or not qmd_list_collections(config.qmd_dir):
        return
    for result in refresh_index(config):
        if not result.success:
            return


def ingest_prepare(
    config: WorkspaceConfig,
    cache: ProposalCache,
    kind: str,
    file_path: str | None = None,
    url: str | None = None,
    context: str | None = None,
) -> dict:
    client = _require_client()
    file: Path | None = None
    if file_path is not None:
        file = Path(file_path).expanduser()
        if not file.is_absolute():
            file = config.root_path / file
    try:
        proposal = prepare_capture(config, kind, client, file=file, url=url, context=context)
    except (IngestError, ModelError) as exc:
        raise ToolError(str(exc)) from exc

    if proposal.duplicate_of is not None:
        return {
            "proposal_id": None,
            "duplicate_of": proposal.duplicate_of,
            "title": None,
            "abstract": None,
            "origin": None,
            "raw_file_path": None,
        }

    proposal_id = cache.put("capture", proposal)
    return {
        "proposal_id": proposal_id,
        "duplicate_of": None,
        "title": proposal.title,
        "abstract": proposal.abstract,
        "origin": proposal.origin,
        "meeting_date": proposal.meeting_date,
        "raw_file_path": proposal.raw_file.path,
        # An authored value wakil declined to use. ADR 0019 moved capture's
        # review moment off the CLI preview and onto the coordinating skill,
        # so a warning that only reaches the preview reaches nobody here.
        "warnings": proposal.warnings,
        # A coordinating agent needs to see this before calling apply, which
        # will refuse. Deliberately no `overwrite` parameter on `ingest_apply`:
        # overwriting a knowledge-base file with no human present is exactly
        # what working-agreement items 11/12 rule out.
        "collision": proposal.collision,
    }


def ingest_apply(config: WorkspaceConfig, cache: ProposalCache, proposal_id: str) -> dict:
    # peek, not pop: everything up to `apply_capture` below is retryable
    # (lock contention, a tree the human dirtied during review, branch
    # resolution), and consuming the proposal before those would leave the
    # client with retry advice it can no longer act on.
    try:
        proposal = cache.peek("capture", proposal_id)
    except ProposalNotFoundError as exc:
        raise ToolError(str(exc)) from exc

    with _git_lock_or_tool_error(config):
        try:
            landing = prepare_landing(config, source_id=None, title=proposal.title, local=False)
        except GitServiceError as exc:
            raise ToolError(str(exc)) from exc

        try:
            assert_landing_intact(config, landing)
        except GitServiceError as exc:
            raise ToolError(str(exc)) from exc
        try:
            result = apply_capture(config, proposal)
        except IngestError as exc:
            abandon_landing(config, landing)
            raise ToolError(str(exc)) from exc
        # Claim only once the write has actually happened. `claim` raises if
        # another concurrent apply of the same id got here first, which is
        # what keeps `peek` single-use across worker threads.
        try:
            cache.claim("capture", proposal_id)
        except ProposalNotFoundError as exc:
            raise ToolError(
                f"Capture {proposal_id} was already applied by a concurrent call."
            ) from exc

        outcome = _land(
            config,
            landing,
            source_id=result.source_id,
            files=[result.raw_file_path],
            title=proposal.title,
            summary=None,
            ingest_run_id=result.ingest_run_id,
            kind="source",
            phase="capture",
        )
    _refresh_qmd(config)
    return {
        "source_id": result.source_id,
        "raw_file_path": result.raw_file_path,
        "branch": outcome.branch if outcome else None,
        "commit_sha": outcome.commit_sha if outcome else None,
        "pr_url": outcome.pr_url if outcome else None,
    }


# --------------------------------------------------------------------------
# Write tools: enrich (prepare/apply)


def _proposed_paths(proposal) -> list[str]:
    paths = [f.path for f in proposal.stub_entities]
    if proposal.proposed_note is not None:
        paths.insert(0, proposal.proposed_note.path)
    return paths


def enrich_prepare(
    config: WorkspaceConfig,
    cache: ProposalCache,
    source_id: int,
    context: str | None = None,
    force: bool = False,
) -> dict:
    client = _require_client()
    # Prepare takes and releases the lock on its own. Holding it until
    # `enrich_apply` would mean any proposal the client never applies -- a
    # declined review, a dropped session, the 1h ProposalCache TTL expiring --
    # wedges the workspace for every other caller. `prepare_landing` is
    # idempotent (it resumes `Source.git_branch`), so apply can simply
    # re-acquire and re-resolve.
    with _git_lock_or_tool_error(config):
        try:
            landing = prepare_landing(
                config, source_id=source_id, title=f"source-{source_id}", local=False
            )
        except GitServiceError as exc:
            raise ToolError(str(exc)) from exc

        try:
            proposal = prepare_enrichment(config, source_id, client, context=context, force=force)
        except (IngestError, ModelError) as exc:
            abandon_landing(config, landing)
            raise ToolError(str(exc)) from exc

        issues = validate_proposal(proposal, kb_root=config.root_path)
        # Either way the working tree goes back to the default branch before
        # control returns to the client -- leaving it parked on an ingest
        # branch across an unbounded gap is what made a later, unrelated
        # command operate on the wrong branch (#181).
        abandon_landing(config, landing)

    if issues:
        return {
            "proposal_id": None,
            "issues": [str(issue) for issue in issues],
            "summary": proposal.summary,
            "key_points": proposal.key_points,
            "files_to_write": _proposed_paths(proposal),
            "entities_resolved": [],
            "memories_to_create": len(proposal.memories),
            "relationships_to_create": len(proposal.relationships),
            "warnings": proposal.warnings,
        }

    proposal_id = cache.put("enrichment", proposal)
    return {
        "proposal_id": proposal_id,
        "issues": [],
        "summary": proposal.summary,
        "key_points": proposal.key_points,
        "files_to_write": _proposed_paths(proposal),
        "entities_resolved": [
            {
                "name": r.name,
                "entity_type": r.entity_type,
                "action": r.action,
                "target_note_path": r.target_note_path,
                "confidence": r.confidence,
                "relevance": r.relevance,
            }
            for r in proposal.entity_resolutions
        ],
        "memories_to_create": len(proposal.memories),
        "relationships_to_create": len(proposal.relationships),
        "warnings": proposal.warnings,
    }


def enrich_apply(config: WorkspaceConfig, cache: ProposalCache, proposal_id: str) -> dict:
    # peek, not pop -- see ingest_apply. This proposal cost two model calls
    # (extraction + resolution), so discarding it on a transient lock failure
    # is expensive as well as wrong.
    try:
        proposal = cache.peek("enrichment", proposal_id)
    except ProposalNotFoundError as exc:
        raise ToolError(str(exc)) from exc

    with _git_lock_or_tool_error(config):
        # Re-resolve rather than replaying a LandingContext built in an
        # earlier tool call: the branch may have been merged and deleted, or
        # HEAD moved, in the interval.
        try:
            landing = prepare_landing(
                config,
                source_id=proposal.source_id,
                title=f"source-{proposal.source_id}",
                local=False,
            )
        except GitServiceError as exc:
            raise ToolError(str(exc)) from exc

        try:
            # The prepare/apply gap is unbounded (ADR 0018), and
            # `apply_enrichment` rewrites existing notes -- check the tree is
            # still ours before it does, not just before the commit.
            assert_landing_intact(config, landing)
        except GitServiceError as exc:
            raise ToolError(str(exc)) from exc

        # Claim before writing: `apply_enrichment` rewrites existing notes, so
        # a concurrent second application is not idempotent. Claiming under
        # the lock is what serializes that.
        try:
            cache.claim("enrichment", proposal_id)
        except ProposalNotFoundError as exc:
            abandon_landing(config, landing)
            raise ToolError(
                f"Enrichment {proposal_id} was already applied by a concurrent call."
            ) from exc

        try:
            result = apply_enrichment(config, proposal)
        except IngestError as exc:
            abandon_landing(config, landing)
            raise ToolError(str(exc)) from exc

        if not result.files_written:
            abandon_landing(config, landing)
            return {
                "files_written": [],
                "memories_created": 0,
                "relationships_created": 0,
                "stale_updates_skipped": result.stale_updates_skipped,
                "branch": None,
                "commit_sha": None,
                "pr_url": None,
            }

        outcome = _land(
            config,
            landing,
            source_id=proposal.source_id,
            files=result.files_written,
            title=proposal.title,
            summary=proposal.summary or None,
            ingest_run_id=result.ingest_run_id,
            kind="ingest",
            phase="enrichment",
        )
    _refresh_qmd(config)
    return {
        "files_written": result.files_written,
        "memories_created": result.memories_created,
        "relationships_created": result.relationships_created,
        "stale_updates_skipped": result.stale_updates_skipped,
        "branch": outcome.branch if outcome else None,
        "commit_sha": outcome.commit_sha if outcome else None,
        "pr_url": outcome.pr_url if outcome else None,
    }
