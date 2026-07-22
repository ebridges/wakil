"""wakil CLI entry point."""

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

import wakil
from wakil.app.workspace_service import get_status, init_workspace
from wakil.config.registry import lookup_workspace, register_workspace
from wakil.config.settings import WorkspaceConfig, find_workspace_root, is_initialized
from wakil.ui.console import (
    console,
    print_capture_proposal,
    print_capture_result,
    print_enrichment_proposal,
    print_enrichment_result,
    print_index_result,
    print_proposal_issues,
    print_query_result,
    print_root_issues,
    print_schema_list,
    print_schema_which,
    print_search_hits,
    print_skill_description,
    print_skill_lint,
    print_skill_list,
    print_skill_validation,
    print_skill_which,
    print_status,
)

if TYPE_CHECKING:
    from wakil.app.context_references import ResolvedContext

WORKSPACE_HELP = (
    "Workspace directory or registered workspace name "
    "(defaults to the current directory, searching upward)."
)

app = typer.Typer(
    name="wakil",
    help="Local-first agent for a personal Markdown knowledge base.",
    no_args_is_help=True,
)
ingest_app = typer.Typer(help="Ingest raw sources into the knowledge base.", no_args_is_help=True)
app.add_typer(ingest_app, name="ingest")
git_app = typer.Typer(help="Git awareness for the knowledge base.", no_args_is_help=True)
app.add_typer(git_app, name="git")
memory_app = typer.Typer(help="Review and manage the memory lifecycle.", no_args_is_help=True)
app.add_typer(memory_app, name="memory")
schema_app = typer.Typer(help="Entity schema tools for the knowledge base.", no_args_is_help=True)
app.add_typer(schema_app, name="schema")
skills_app = typer.Typer(help="Discover, inspect, and validate skills.", no_args_is_help=True)
app.add_typer(skills_app, name="skills")
qmd_app = typer.Typer(help="Manage the QMD search index and collections.", no_args_is_help=True)
app.add_typer(qmd_app, name="qmd")
qmd_collection_app = typer.Typer(
    help="Manage QMD collections (indexed folders).", no_args_is_help=True
)
qmd_app.add_typer(qmd_collection_app, name="collection")
sources_app = typer.Typer(help="Maintain captured sources.", no_args_is_help=True)
app.add_typer(sources_app, name="sources")


@app.callback()
def main(
    ctx: typer.Context,
    workspace: Annotated[
        str | None,
        typer.Option("--workspace", "-w", envvar="WAKIL_WORKSPACE", help=WORKSPACE_HELP),
    ] = None,
) -> None:
    """Local-first agent for a personal Markdown knowledge base."""
    ctx.obj = workspace


def _resolve_workspace(ctx: typer.Context) -> Path:
    """Resolve -w/--workspace (path or registered name) to an initialized root."""
    value: str | None = ctx.obj
    if value is None:
        root = find_workspace_root(Path.cwd())
        if root is None:
            console.print(
                "[red]No wakil workspace found in the current directory.[/red] "
                "Run [bold]wakil init <path>[/bold] first, or pass "
                "[bold]-w <path-or-name>[/bold]."
            )
            raise typer.Exit(code=1)
        return root

    candidate = Path(value).expanduser()
    if candidate.is_dir():
        root = find_workspace_root(candidate)
        if root is None:
            console.print(
                f"[red]{candidate} is not inside an initialized wakil workspace.[/red] "
                "Run [bold]wakil init <path>[/bold] first."
            )
            raise typer.Exit(code=1)
        return root

    registered = lookup_workspace(value)
    if registered is None:
        console.print(
            f"[red]Unknown workspace: {value}[/red] — not a directory and not a "
            "registered workspace name. Run [bold]wakil init <path>[/bold] to register one."
        )
        raise typer.Exit(code=1)
    if not is_initialized(registered):
        console.print(
            f"[red]Registered workspace '{value}' points to {registered}, which is no "
            "longer initialized.[/red] Re-run [bold]wakil init[/bold] there."
        )
        raise typer.Exit(code=1)
    return registered


@app.command()
def init(
    path: Annotated[Path, typer.Argument(help="Knowledge-base directory to initialize.")] = Path(
        "."
    ),
    name: Annotated[
        str | None,
        typer.Option("--name", help="Workspace name (defaults to directory name)."),
    ] = None,
    no_qmd_collection: Annotated[
        bool,
        typer.Option(
            "--no-qmd-collection",
            help="Skip creating the default QMD collection for the whole workspace.",
        ),
    ] = False,
) -> None:
    """Initialize a knowledge-base workspace and index its Markdown files."""
    status, index_result = init_workspace(path, name=name)
    register_workspace(status.config.name, status.config.root_path)
    console.print(
        f"Workspace [bold]{status.config.name}[/bold] ready at {status.config.root_path} "
        f"[dim](use -w {status.config.name} from anywhere)[/dim]"
    )
    print_index_result(index_result)

    if status.config.qmd_enabled and not no_qmd_collection:
        from wakil.app.qmd_service import ensure_default_collection

        result = ensure_default_collection(status.config)
        if result is not None:
            if result.success:
                console.print(f"[green]QMD collection registered:[/green] {result.message}")
                status.qmd.project_index = True
            else:
                console.print(f"[yellow]QMD collection not created:[/yellow] {result.message}")
    print_status(status)


@app.command()
def status(ctx: typer.Context) -> None:
    """Show workspace status: notes, git state, QMD availability."""
    print_status(get_status(_resolve_workspace(ctx)))


@app.command()
def index(ctx: typer.Context) -> None:
    """Re-index Markdown files into the workspace database."""
    _, index_result = init_workspace(_resolve_workspace(ctx))
    print_index_result(index_result)


@app.command()
def search(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="Free-text search query.")],
    limit: Annotated[int, typer.Option("--limit", help="Max results per engine.")] = 10,
    mode: Annotated[
        str,
        typer.Option("--mode", help="QMD mode: search (BM25), vsearch (vector), query (hybrid)."),
    ] = "search",
) -> None:
    """Search the knowledge base via QMD plus local FTS indexes."""
    from wakil.app.search_service import search_workspace
    from wakil.app.workspace_service import open_session

    root = _resolve_workspace(ctx)
    config = WorkspaceConfig.load(root)
    with open_session(config) as session:
        hits = search_workspace(session, config, query, limit=limit, mode=mode)
    print_search_hits(hits, query)
    if not config.qmd_enabled:
        console.print("[dim]QMD not detected; results are from local indexes only.[/dim]")


@app.command()
def query(
    ctx: typer.Context,
    question: Annotated[str, typer.Argument(help="Question to answer from the knowledge base.")],
    limit: Annotated[int, typer.Option("--limit", help="Max search hits to consider.")] = 10,
    mode: Annotated[
        str,
        typer.Option("--mode", help="QMD mode: search (BM25), vsearch (vector), query (hybrid)."),
    ] = "search",
) -> None:
    """Answer a question with grounded citations from the knowledge base."""
    from wakil.app.query_service import run_query
    from wakil.llm.client import ModelError, resolve_client

    root = _resolve_workspace(ctx)
    config = WorkspaceConfig.load(root)
    client = resolve_client()
    if client is None:
        console.print(
            "[red]No model provider configured.[/red] Set [bold]ANTHROPIC_API_KEY[/bold] "
            "(or OPENAI_API_KEY + WAKIL_MODEL for an OpenAI-compatible endpoint)."
        )
        raise typer.Exit(code=1)
    try:
        with console.status(f"Querying with {client.model}..."):
            result = run_query(config, question, client, limit=limit, mode=mode)
    except ModelError as exc:
        console.print(f"[red]Model error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    print_query_result(result)


def _land_written_files(
    config: WorkspaceConfig,
    landing,
    *,
    source_id: int,
    files: list[str],
    title: str,
    summary: str | None,
    ingest_run_id: int,
    kind: str,
    phase: str,
) -> None:
    from wakil.app.git_service import GitServiceError, land_ingestion

    if landing.local:
        console.print("[dim]--local: files written, not committed.[/dim]")
        return
    try:
        outcome = land_ingestion(
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
        console.print(
            f"[red]Landing failed:[/red] {exc}\n"
            "[dim]The written files are still on disk for manual review.[/dim]"
        )
        raise typer.Exit(code=1) from exc
    location = f" on [bold]{outcome.branch}[/bold]" if outcome.branch else ""
    console.print(f"Committed [bold]{outcome.commit_sha[:10]}[/bold]{location}")
    if outcome.pr_url:
        console.print(f"PR: {outcome.pr_url}")
    if outcome.returned_to:
        console.print(f"[dim]Returned to {outcome.returned_to}.[/dim]")


def _refresh_qmd_index(config: WorkspaceConfig) -> None:
    """Keep QMD current after writing files: re-scan collections, then embed
    anything new. Best-effort — the ingest itself already succeeded by the
    time this runs, so a refresh failure only warns, never fails the command.
    Not wrapped in a spinner: the embed step streams qmd's own progress bar
    live, which would visually collide with a Rich spinner running at the
    same time."""
    from wakil.app.qmd_service import refresh_index
    from wakil.integrations.qmd import qmd_list_collections

    if not config.qmd_enabled or not qmd_list_collections(config.qmd_dir):
        return
    console.print(
        "[dim]Refreshing QMD index (embedding may take a while on first run "
        "— downloading the embedding model)...[/dim]"
    )
    results = refresh_index(config)
    for result in results:
        if not result.success:
            console.print(f"[yellow]QMD index refresh incomplete:[/yellow] {result.message}")
            return
    console.print("[dim]QMD index refreshed.[/dim]")


def _resolve_context_or_exit(
    context: list[str] | None, context_file: list[Path] | None, workspace_root: Path
) -> "ResolvedContext | None":
    from wakil.app.context_references import ContextResolutionError, resolve_context

    try:
        resolved, warnings = resolve_context(
            context=context or [],
            context_files=context_file or [],
            workspace_root=workspace_root,
        )
    except ContextResolutionError as exc:
        console.print(f"[red]Context resolution failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    for warning in warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}")
    return resolved


def _run_ingest(
    ctx: typer.Context,
    kind: str,
    yes: bool,
    file: Path | None = None,
    url: str | None = None,
    local: bool = False,
    context: list[str] | None = None,
    context_file: list[Path] | None = None,
) -> None:
    """Step 1: capture the raw source. Deterministic except for one small
    model call that generates the frontmatter title/abstract (ADR 0010) —
    the raw file's path/slug itself stays fully deterministic.

    Branches, commits, and opens a draft PR by default; --local writes the
    raw file only, with no git operations.
    """
    from wakil.app.git_service import GitServiceError, abandon_landing, prepare_landing
    from wakil.app.ingest_service import IngestError, apply_capture, prepare_capture
    from wakil.llm.client import ModelError, resolve_client

    root = _resolve_workspace(ctx)
    config = WorkspaceConfig.load(root)
    resolved_context = _resolve_context_or_exit(context, context_file, config.root_path)
    client = resolve_client()
    if client is None:
        console.print(
            "[red]Ingest needs a model provider.[/red] Set [bold]ANTHROPIC_API_KEY[/bold] "
            "(or OPENAI_API_KEY + WAKIL_MODEL for an OpenAI-compatible endpoint)."
        )
        raise typer.Exit(code=1)
    try:
        with console.status("Preparing capture..."):
            proposal = prepare_capture(
                config,
                kind,
                client,
                file=file,
                url=url,
                context=resolved_context.text if resolved_context else None,
                context_digest=resolved_context.digest if resolved_context else None,
                context_referenced_paths=(
                    resolved_context.referenced_paths if resolved_context else None
                ),
            )
    except (IngestError, ModelError) as exc:
        console.print(f"[red]Ingest failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if proposal.duplicate_of is not None:
        console.print(
            f"[yellow]Already ingested[/yellow] (matches source #{proposal.duplicate_of}); "
            "nothing to do."
        )
        return

    print_capture_proposal(proposal)
    if not yes and not typer.confirm("Write this raw capture and record the source?"):
        console.print("Aborted; nothing was written.")
        raise typer.Exit(code=0)

    try:
        landing = prepare_landing(config, source_id=None, title=proposal.title, local=local)
        if landing.branch:
            console.print(f"On branch [bold]{landing.branch}[/bold]")
    except GitServiceError as exc:
        console.print(f"[red]Ingest failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    try:
        result = apply_capture(config, proposal)
    except IngestError as exc:
        console.print(f"[red]Ingest failed:[/red] {exc}")
        abandon_landing(config, landing)
        raise typer.Exit(code=1) from exc
    print_capture_result(result)

    _land_written_files(
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
    _refresh_qmd_index(config)


_CONTEXT = Annotated[
    list[str] | None,
    typer.Option(
        "--context",
        "-C",
        help="Context for the model (attendees, company, purpose); repeatable. "
        "Supports @file:PATH (optionally #Heading or :start-end) and @url:URL "
        "references, expanded inline before use.",
    ),
]
_CONTEXT_FILE = Annotated[
    list[Path] | None,
    typer.Option(
        "--context-file",
        help="Read a file's full text as context; repeatable. The same "
        "@file:/@url: expansion applies inside the file's content.",
    ),
]


@app.command()
def enrich(
    ctx: typer.Context,
    source_id: Annotated[int, typer.Argument(help="Source id from the capture step.")],
    context: _CONTEXT = None,
    context_file: _CONTEXT_FILE = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Re-analyze a source that was already enriched."),
    ] = False,
    local: Annotated[
        bool,
        typer.Option(
            "--local", "-l", help="Write files without branching, committing, or opening a PR."
        ),
    ] = False,
) -> None:
    """Step 2: analyze a captured source and link it into the knowledge base.

    Lands on the same branch/PR the capture step started (or a fresh one if
    the source was captured with --local), flipping a draft PR to ready for
    review. --local writes files only, with no git operations.
    """
    from wakil.app.git_service import GitServiceError, abandon_landing, prepare_landing
    from wakil.app.ingest_service import (
        IngestError,
        apply_enrichment,
        prepare_enrichment,
        validate_proposal,
    )
    from wakil.llm.client import ModelError, resolve_client

    root = _resolve_workspace(ctx)
    config = WorkspaceConfig.load(root)
    resolved_context = _resolve_context_or_exit(context, context_file, config.root_path)
    client = resolve_client()
    if client is None:
        console.print(
            "[red]Enrichment needs a model provider.[/red] Set [bold]ANTHROPIC_API_KEY[/bold] "
            "(or OPENAI_API_KEY + WAKIL_MODEL for an OpenAI-compatible endpoint)."
        )
        raise typer.Exit(code=1)

    # Resolve/switch onto the source's branch *before* reading anything --
    # the raw capture prepare_enrichment reads back was committed there, not
    # on whatever branch this session started on.
    try:
        landing = prepare_landing(
            config, source_id=source_id, title=f"source-{source_id}", local=local
        )
        if landing.branch:
            console.print(f"On branch [bold]{landing.branch}[/bold]")
    except GitServiceError as exc:
        console.print(f"[red]Enrichment failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    try:
        with console.status(f"Analyzing source #{source_id} with {client.model}..."):
            proposal = prepare_enrichment(
                config,
                source_id,
                client,
                context=resolved_context.text if resolved_context else None,
                context_digest=resolved_context.digest if resolved_context else None,
                context_referenced_paths=(
                    resolved_context.referenced_paths if resolved_context else None
                ),
                force=force,
            )
    except (IngestError, ModelError) as exc:
        console.print(f"[red]Enrichment failed:[/red] {exc}")
        abandon_landing(config, landing)
        raise typer.Exit(code=1) from exc

    print_enrichment_proposal(proposal)
    issues = validate_proposal(proposal, kb_root=config.root_path)
    if issues:
        print_proposal_issues(issues)
        abandon_landing(config, landing)
        raise typer.Exit(code=1)
    if not yes and not typer.confirm("Apply this enrichment (write files, record memories)?"):
        console.print("Aborted; nothing was written.")
        abandon_landing(config, landing)
        raise typer.Exit(code=0)

    try:
        result = apply_enrichment(config, proposal)
    except IngestError as exc:
        console.print(f"[red]Enrichment failed:[/red] {exc}")
        abandon_landing(config, landing)
        raise typer.Exit(code=1) from exc
    print_enrichment_result(result)

    if result.files_written:
        _land_written_files(
            config,
            landing,
            source_id=source_id,
            files=result.files_written,
            title=proposal.title,
            summary=proposal.summary or None,
            ingest_run_id=result.ingest_run_id,
            kind="ingest",
            phase="enrichment",
        )
        _refresh_qmd_index(config)
    else:
        abandon_landing(config, landing)
        console.print("[dim]No files were written; nothing to land.[/dim]")


_YES = Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")]
_LOCAL = Annotated[
    bool,
    typer.Option(
        "--local", "-l", help="Write files without branching, committing, or opening a PR."
    ),
]


@ingest_app.command("transcript")
def ingest_transcript(
    ctx: typer.Context,
    file: Annotated[Path, typer.Argument(help="Transcript file (.txt, .md, .srt, or .whisper).")],
    context: _CONTEXT = None,
    context_file: _CONTEXT_FILE = None,
    yes: _YES = False,
    local: _LOCAL = False,
) -> None:
    """Ingest a meeting or call transcript."""
    _run_ingest(
        ctx,
        "transcript",
        yes,
        file=file,
        local=local,
        context=context,
        context_file=context_file,
    )


@ingest_app.command("text")
def ingest_text(
    ctx: typer.Context,
    file: Annotated[Path, typer.Argument(help="Text or Markdown file to ingest.")],
    context: _CONTEXT = None,
    context_file: _CONTEXT_FILE = None,
    yes: _YES = False,
    local: _LOCAL = False,
) -> None:
    """Ingest a plain text file, pasted note, or clipping."""
    _run_ingest(
        ctx,
        "text",
        yes,
        file=file,
        local=local,
        context=context,
        context_file=context_file,
    )


@ingest_app.command("article")
def ingest_article(
    ctx: typer.Context,
    url: Annotated[str, typer.Argument(help="Web article URL.")],
    context: _CONTEXT = None,
    context_file: _CONTEXT_FILE = None,
    yes: _YES = False,
    local: _LOCAL = False,
) -> None:
    """Fetch a web article, extract its text, and ingest it."""
    _run_ingest(
        ctx,
        "article",
        yes,
        url=url,
        local=local,
        context=context,
        context_file=context_file,
    )


@sources_app.command("backfill-abstract")
def sources_backfill_abstract(
    ctx: typer.Context,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Backfill title/abstract for sources captured before ADR 0010.

    Metadata-only: rewrites each raw file's frontmatter (title/abstract
    keys) and the matching Source row. Never re-runs enrichment.
    """
    from wakil.app.ingest_service import (
        IngestError,
        apply_abstract_backfill,
        plan_abstract_backfill,
    )
    from wakil.llm.client import ModelError, resolve_client
    from wakil.ui.console import print_abstract_backfill_plan

    root = _resolve_workspace(ctx)
    config = WorkspaceConfig.load(root)
    client = resolve_client()
    if client is None:
        console.print(
            "[red]Backfill needs a model provider.[/red] Set [bold]ANTHROPIC_API_KEY[/bold] "
            "(or OPENAI_API_KEY + WAKIL_MODEL for an OpenAI-compatible endpoint)."
        )
        raise typer.Exit(code=1)

    try:
        with console.status("Scanning sources for a missing abstract..."):
            items = plan_abstract_backfill(config, client)
    except (IngestError, ModelError) as exc:
        console.print(f"[red]Backfill failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    print_abstract_backfill_plan(items)
    if not items:
        return
    if not yes and not typer.confirm(f"Rewrite title/abstract for {len(items)} source(s)?"):
        console.print("Aborted; nothing was written.")
        raise typer.Exit(code=0)

    updated = apply_abstract_backfill(config, items)
    console.print(f"[green]Updated {len(updated)} source(s).[/green]")


def _memory_session(ctx: typer.Context):
    """(config, session, workspace_id) for memory commands."""
    from wakil.app.search_service import get_workspace_id
    from wakil.app.workspace_service import open_session

    root = _resolve_workspace(ctx)
    config = WorkspaceConfig.load(root)
    session = open_session(config)
    workspace_id = get_workspace_id(session, config)
    if workspace_id is None:
        session.close()
        console.print("[red]Workspace database is not initialized; run wakil init first.[/red]")
        raise typer.Exit(code=1)
    return session, workspace_id


def _transition(ctx: typer.Context, ids: list[int], new_state: str) -> None:
    from wakil.app.memory_service import MemoryError, transition_memories
    from wakil.ui.console import print_transitions

    session, workspace_id = _memory_session(ctx)
    with session:
        try:
            results = transition_memories(session, workspace_id, ids, new_state)
        except MemoryError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        session.commit()
    print_transitions(results)


@memory_app.command("list")
def memory_list(
    ctx: typer.Context,
    state: Annotated[
        str | None,
        typer.Option(
            "--state",
            help="Filter by state: working|candidate|durable|rejected|archived.",
        ),
    ] = None,
    memory_type: Annotated[
        str | None,
        typer.Option("--type", help="Filter by memory type (fact, decision, ...)."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max memories to show.")] = 50,
) -> None:
    """List memories, newest first."""
    from wakil.app.memory_service import MemoryError, list_memories
    from wakil.ui.console import print_memories

    session, workspace_id = _memory_session(ctx)
    with session:
        try:
            memories = list_memories(
                session, workspace_id, state=state, memory_type=memory_type, limit=limit
            )
        except MemoryError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        print_memories(memories)


@memory_app.command("show")
def memory_show(
    ctx: typer.Context,
    memory_id: Annotated[int, typer.Argument(help="Memory id (see wakil memory list).")],
) -> None:
    """Show one memory in full."""
    from wakil.app.memory_service import MemoryError, get_memory
    from wakil.ui.console import print_memory_detail

    session, workspace_id = _memory_session(ctx)
    with session:
        try:
            memory = get_memory(session, workspace_id, memory_id)
        except MemoryError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        print_memory_detail(memory)


@memory_app.command("promote")
def memory_promote(
    ctx: typer.Context,
    ids: Annotated[list[int], typer.Argument(help="Memory ids to promote to durable.")],
) -> None:
    """Promote memories to durable so they shape future answers."""
    _transition(ctx, ids, "durable")


@memory_app.command("reject")
def memory_reject(
    ctx: typer.Context,
    ids: Annotated[list[int], typer.Argument(help="Memory ids to reject.")],
) -> None:
    """Reject memory proposals; rejected memories are excluded from search."""
    _transition(ctx, ids, "rejected")


@memory_app.command("archive")
def memory_archive(
    ctx: typer.Context,
    ids: Annotated[list[int], typer.Argument(help="Memory ids to archive.")],
) -> None:
    """Archive memories: kept and searchable, but downranked."""
    _transition(ctx, ids, "archived")


@schema_app.command("migrate")
def schema_migrate(
    ctx: typer.Context,
    entity_type: Annotated[
        str | None,
        typer.Option("--type", help="Migrate only notes of this entity type."),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the plan and diffs; write nothing.")
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Apply every proposed fix without prompting."),
    ] = False,
    commit: Annotated[
        bool,
        typer.Option("--commit", "-c", help="Commit each type's fixes (wakil chore: ...)."),
    ] = False,
) -> None:
    """Propose cheap-tier frontmatter fixes and apply them per type on confirm."""
    from wakil.app.git_service import GitServiceError, commit_change
    from wakil.app.schema_migrate_service import (
        MigrateError,
        apply_migrations,
        plan_schema_migration,
    )
    from wakil.ui.console import print_migration_diffs, print_migration_plan

    root = _resolve_workspace(ctx)
    config = WorkspaceConfig.load(root)
    try:
        with console.status("Scanning indexed notes..."):
            plan = plan_schema_migration(config, entity_type=entity_type)
    except MigrateError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    print_migration_plan(plan)
    if plan.total_files == 0:
        return
    if dry_run:
        for proposals in plan.by_type.values():
            print_migration_diffs(proposals)
        console.print("[dim]Dry run: nothing was written.[/dim]")
        return

    for type_name, proposals in sorted(plan.by_type.items()):
        if not yes:
            applied = False
            while True:
                answer = typer.prompt(
                    f"Apply {len(proposals)} fix(es) to {type_name} files? [y/N/d=show diffs]",
                    default="n",
                ).lower()
                if answer == "d":
                    print_migration_diffs(proposals)
                    continue
                applied = answer == "y"
                break
            if not applied:
                console.print(f"[dim]Skipped {type_name}.[/dim]")
                continue
        written, stale = apply_migrations(config, proposals)
        for message in stale:
            console.print(f"[yellow]{message}[/yellow]")
        console.print(f"Rewrote [bold]{len(written)}[/bold] {type_name} file(s).")
        if commit and written:
            try:
                outcome = commit_change(
                    config, written, "chore", f"normalize {type_name} frontmatter"
                )
            except GitServiceError as exc:
                console.print(f"[red]Commit failed:[/red] {exc}")
                raise typer.Exit(code=1) from exc
            console.print(f"Committed [bold]{outcome.commit_sha[:10]}[/bold]")


@schema_app.command("list")
def schema_list(ctx: typer.Context) -> None:
    """List effective entity types and their resolved source (kb-local/user/builtin)."""
    from wakil.schema.loader import SchemaLoadError, load_entity_schemas, resolve_entity_schema

    root = _resolve_workspace(ctx)
    try:
        schemas = load_entity_schemas(root)
    except SchemaLoadError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    rows: list[tuple[str, str, str]] = []
    for entity_type in sorted(schemas):
        resolved = resolve_entity_schema(entity_type, root)
        if resolved is None:
            continue
        _, schema_root = resolved
        rows.append((entity_type, schema_root.source, str(schema_root.path)))
    print_schema_list(rows)


@schema_app.command("which")
def schema_which(
    ctx: typer.Context,
    entity_type: Annotated[str, typer.Argument(help="Entity type to resolve.")],
) -> None:
    """Show which root's schema file wins for an entity type."""
    from wakil.schema.loader import resolve_entity_schema

    root = _resolve_workspace(ctx)
    resolved = resolve_entity_schema(entity_type, root)
    if resolved is None:
        console.print(f"[red]No entity schema defines type {entity_type!r}.[/red]")
        raise typer.Exit(code=1)
    _, schema_root = resolved
    print_schema_which(entity_type, schema_root.source, str(schema_root.path))


@git_app.command("summary")
def git_summary(ctx: typer.Context) -> None:
    """Show branch, pending changes, recent commits, and wakil branches."""
    from wakil.integrations import git as git_integration

    root = _resolve_workspace(ctx)
    info = git_integration.inspect_git(root)
    if not info.is_repo:
        console.print("[yellow]This workspace is not a git repository.[/yellow]")
        raise typer.Exit(code=1)

    console.print(f"Branch: [bold]{info.branch}[/bold]")
    changed = git_integration.changed_files(root)
    if changed:
        console.print(f"[yellow]{len(changed)} uncommitted change(s):[/yellow]")
        for line in changed[:20]:
            console.print(f"  {line}")
    else:
        console.print("[green]Working tree clean[/green]")
    if info.recent_commits:
        console.print("[bold]Recent commits:[/bold]")
        for line in info.recent_commits:
            console.print(f"  [dim]{line}[/dim]")
    branches = git_integration.wakil_branches(root)
    if branches:
        console.print("[bold]wakil branches:[/bold]")
        for name in branches:
            console.print(f"  {name}")


@git_app.command("history")
def git_history(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="Workspace-relative file path.")],
    limit: Annotated[int, typer.Option("--limit", help="Max commits to show.")] = 10,
) -> None:
    """Show the commit history of one note or file."""
    from wakil.integrations import git as git_integration

    root = _resolve_workspace(ctx)
    if not git_integration.inspect_git(root).is_repo:
        console.print("[yellow]This workspace is not a git repository.[/yellow]")
        raise typer.Exit(code=1)
    entries = git_integration.file_history(root, path, limit=limit)
    if not entries:
        console.print(f"No git history for [bold]{path}[/bold].")
        raise typer.Exit(code=0)
    for line in entries:
        console.print(line)


@skills_app.command("list")
def skills_list(ctx: typer.Context) -> None:
    """List effective skills and their resolved source."""
    from wakil.skills.errors import SkillResolutionError
    from wakil.skills.resolver import (
        default_context,
        discover_skill_names,
        resolve_roots,
        resolve_skill,
    )

    root = _resolve_workspace(ctx)
    context = default_context(root)
    root_resolution = resolve_roots(context)
    print_root_issues(root_resolution.issues)

    rows: list[tuple[str, str, str]] = []
    for name in discover_skill_names(context):
        try:
            resolved = resolve_skill(name, context)
        except SkillResolutionError as exc:
            rows.append((name, "error", f"{exc.reason}: {exc}"))
        else:
            rows.append((resolved.name, resolved.source, str(resolved.directory)))
    print_skill_list(rows)


@skills_app.command("which")
def skills_which(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Skill name to resolve.")],
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show ordered search roots and shadowed matches."),
    ] = False,
) -> None:
    """Show which skill directory wins for a name."""
    from wakil.skills.errors import SkillResolutionError
    from wakil.skills.resolver import (
        default_context,
        find_shadowed_roots,
        resolve_roots,
        resolve_skill,
    )

    root = _resolve_workspace(ctx)
    context = default_context(root)
    root_resolution = resolve_roots(context)
    print_root_issues(root_resolution.issues)
    try:
        resolved = resolve_skill(name, context)
    except SkillResolutionError as exc:
        console.print(f"[red]{exc.reason}:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    roots = root_resolution.roots if verbose else None
    shadowed = find_shadowed_roots(name, context) if verbose else None
    print_skill_which(resolved, verbose=verbose, roots=roots, shadowed=shadowed)


@skills_app.command("describe")
def skills_describe(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Skill name to describe.")],
) -> None:
    """Provide a description of the skill."""
    from wakil.skills.errors import SkillResolutionError
    from wakil.skills.resolver import (
        default_context,
        resolve_roots,
        resolve_skill,
    )

    root = _resolve_workspace(ctx)
    context = default_context(root)
    root_resolution = resolve_roots(context)
    print_root_issues(root_resolution.issues)
    try:
        resolved = resolve_skill(name, context)
    except SkillResolutionError as exc:
        console.print(f"[red]{exc.reason}:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    print_skill_description(resolved)


@skills_app.command("validate")
def skills_validate(
    ctx: typer.Context,
    name: Annotated[
        str | None,
        typer.Argument(help="Validate only this skill (defaults to every discovered skill)."),
    ] = None,
) -> None:
    """Validate skill roots and skill directories."""
    from wakil.skills.errors import SkillResolutionError
    from wakil.skills.resolver import (
        default_context,
        discover_skill_names,
        resolve_roots,
        resolve_skill,
    )

    root = _resolve_workspace(ctx)
    context = default_context(root)
    root_resolution = resolve_roots(context)

    names = [name] if name else discover_skill_names(context)
    results: list[tuple[str, bool, str]] = []
    for candidate in names:
        try:
            resolved = resolve_skill(candidate, context)
        except SkillResolutionError as exc:
            results.append((candidate, False, f"{exc.reason}: {exc}"))
        else:
            results.append((candidate, True, f"{resolved.source}: {resolved.directory}"))

    print_skill_validation(root_resolution.issues, results)
    if root_resolution.issues or any(not ok for _, ok, _ in results):
        raise typer.Exit(code=1)


@skills_app.command("lint")
def skills_lint(
    ctx: typer.Context,
    name: Annotated[
        str | None,
        typer.Argument(help="Lint only this skill (defaults to every discovered skill)."),
    ] = None,
) -> None:
    """Run deterministic content-quality checks against the skill catalog.

    Extends `skills validate`'s structural checks (frontmatter parses, name
    matches directory, skill_api supported) with content-quality checks: body
    length, description shape, time-sensitive phrasing, dangling
    cross-references, and orphaned support files. No model calls, no network.
    """
    from wakil.skills.errors import SkillResolutionError
    from wakil.skills.lint import LintFinding, builtin_catalog_names, lint_skill
    from wakil.skills.resolver import (
        default_context,
        discover_skill_names,
        resolve_skill,
    )

    root = _resolve_workspace(ctx)
    context = default_context(root)
    catalog_names = builtin_catalog_names(context.builtin_skill_root)

    names = [name] if name else discover_skill_names(context)
    findings: list[LintFinding] = []
    for candidate in names:
        try:
            resolved = resolve_skill(candidate, context)
        except SkillResolutionError as exc:
            findings.append(LintFinding(candidate, "resolution", f"{exc.reason}: {exc}"))
            continue
        findings.extend(lint_skill(resolved, catalog_names))

    print_skill_lint(findings)
    if findings:
        raise typer.Exit(code=1)


@qmd_collection_app.command("add")
def qmd_collection_add(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(help="Folder to index, relative to the workspace root.")],
    name: Annotated[
        str | None, typer.Option("--name", help="Collection name (defaults to the folder name).")
    ] = None,
    pattern: Annotated[
        str | None, typer.Option("--pattern", help="Glob pattern to index (default: **/*.md).")
    ] = None,
) -> None:
    """Register a folder as a QMD collection."""
    from wakil.app.qmd_service import QmdPathError, add_collection

    root = _resolve_workspace(ctx)
    config = WorkspaceConfig.load(root)
    try:
        result = add_collection(config, path, name=name, pattern=pattern)
    except QmdPathError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    if not result.success:
        console.print(f"[red]{result.message}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]{result.message}[/green]" if result.message else "[green]Done.[/green]")


@qmd_collection_app.command("list")
def qmd_collection_list(ctx: typer.Context) -> None:
    """List registered QMD collections."""
    from wakil.integrations.qmd import qmd_list_collections
    from wakil.ui.console import print_qmd_collections

    root = _resolve_workspace(ctx)
    config = WorkspaceConfig.load(root)
    print_qmd_collections(qmd_list_collections(config.qmd_dir))


@qmd_collection_app.command("remove")
def qmd_collection_remove(
    ctx: typer.Context, name: Annotated[str, typer.Argument(help="Collection name to remove.")]
) -> None:
    """Remove a registered QMD collection."""
    from wakil.integrations.qmd import qmd_remove_collection

    root = _resolve_workspace(ctx)
    config = WorkspaceConfig.load(root)
    result = qmd_remove_collection(config.qmd_dir, name)
    if not result.success:
        console.print(f"[red]{result.message}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]{result.message}[/green]" if result.message else "[green]Done.[/green]")


@qmd_app.command("sync")
def qmd_sync(
    ctx: typer.Context,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Register every proposed collection without prompting."),
    ] = False,
) -> None:
    """Propose registering a single QMD collection covering the whole
    workspace, but only when no collection exists yet (usually already done
    by `wakil init`, unless run with --no-qmd-collection)."""
    from wakil.app.qmd_service import add_collection, plan_default_collections
    from wakil.ui.console import print_qmd_collection_plan

    root = _resolve_workspace(ctx)
    config = WorkspaceConfig.load(root)
    plans = plan_default_collections(config)
    print_qmd_collection_plan(plans)
    if not plans:
        return
    if not yes and not typer.confirm(f"Register {len(plans)} collection(s)?"):
        console.print("[dim]Skipped.[/dim]")
        return
    for plan in plans:
        result = add_collection(config, Path(plan.path), name=plan.name, pattern=plan.pattern)
        if result.success:
            console.print(f"[green]✓[/green] {plan.name}")
        else:
            console.print(f"[red]✗ {plan.name}: {result.message}[/red]")


@qmd_app.command("embed")
def qmd_embed(ctx: typer.Context) -> None:
    """Generate embeddings for indexed content that doesn't have one yet.

    Needed for --mode vsearch/query; BM25 --mode search doesn't use it. The
    first run downloads the embedding model, so this may take a while —
    qmd's own progress bar is shown live rather than hidden.
    """
    from wakil.integrations.qmd import qmd_embed as run_qmd_embed

    root = _resolve_workspace(ctx)
    config = WorkspaceConfig.load(root)
    console.print(
        "[dim]Embedding QMD content — first run downloads the embedding model "
        "from Hugging Face, which can take a few minutes.[/dim]"
    )
    result = run_qmd_embed(config.qmd_dir, config.root_path)
    if result.message:
        console.print(result.message)
    if not result.success:
        raise typer.Exit(code=1)
    console.print("[green]Done.[/green]")


@app.command()
def version() -> None:
    """Print the wakil version."""
    console.print(f"wakil {wakil.__version__}")


if __name__ == "__main__":
    app()
