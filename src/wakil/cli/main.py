"""wakil CLI entry point."""

from pathlib import Path
from typing import Annotated

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
    print_search_hits,
    print_skill_list,
    print_skill_validation,
    print_skill_which,
    print_status,
)

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
        str | None, typer.Option("--name", help="Workspace name (defaults to directory name).")
    ] = None,
) -> None:
    """Initialize a knowledge-base workspace and index its Markdown files."""
    status, index_result = init_workspace(path, name=name)
    register_workspace(status.config.name, status.config.root_path)
    console.print(
        f"Workspace [bold]{status.config.name}[/bold] ready at {status.config.root_path} "
        f"[dim](use -w {status.config.name} from anywhere)[/dim]"
    )
    print_index_result(index_result)
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


def _commit_written_files(
    config: WorkspaceConfig,
    files: list[str],
    title: str,
    summary: str | None,
    ingest_run_id: int,
    branch_name: str | None,
    pr: bool,
    kind: str,
) -> None:
    from wakil.app.git_service import GitServiceError, commit_ingest

    try:
        outcome = commit_ingest(
            config,
            files,
            title,
            summary,
            ingest_run_id=ingest_run_id,
            branch=branch_name,
            open_pr=pr,
            kind=kind,
        )
    except GitServiceError as exc:
        console.print(
            f"[red]Commit failed:[/red] {exc}\n"
            "[dim]The written files are still on disk for manual review.[/dim]"
        )
        raise typer.Exit(code=1) from exc
    location = f" on [bold]{outcome.branch}[/bold]" if outcome.branch else ""
    console.print(f"Committed [bold]{outcome.commit_sha[:10]}[/bold]{location}")
    if outcome.pr_url:
        console.print(f"Opened PR: {outcome.pr_url}")


def _run_ingest(
    ctx: typer.Context,
    kind: str,
    yes: bool,
    file: Path | None = None,
    url: str | None = None,
    branch: bool = False,
    commit: bool = False,
    pr: bool = False,
    context: str | None = None,
) -> None:
    """Step 1: capture the raw source. Deterministic — no model involved."""
    from wakil.app.git_service import GitServiceError, start_ingest_branch
    from wakil.app.ingest_service import IngestError, apply_capture, prepare_capture

    root = _resolve_workspace(ctx)
    config = WorkspaceConfig.load(root)
    if pr:
        branch = True
    try:
        with console.status("Preparing capture..."):
            proposal = prepare_capture(config, kind, file=file, url=url, context=context)
    except IngestError as exc:
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

    branch_name: str | None = None
    try:
        if branch:
            branch_name = start_ingest_branch(config, proposal.title)
            console.print(f"Created branch [bold]{branch_name}[/bold]")
        result = apply_capture(config, proposal)
    except (IngestError, GitServiceError) as exc:
        console.print(f"[red]Ingest failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    print_capture_result(result)

    if branch or commit:
        _commit_written_files(
            config,
            [result.raw_file_path],
            proposal.title,
            None,
            result.ingest_run_id,
            branch_name,
            pr,
            kind="source",
        )


@app.command()
def enrich(
    ctx: typer.Context,
    source_id: Annotated[int, typer.Argument(help="Source id from the capture step.")],
    context: Annotated[
        str | None,
        typer.Option(
            "--context",
            "-C",
            help="Extra context (attendees, company, purpose); defaults to the "
            "context given at capture time.",
        ),
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Re-analyze a source that was already enriched.")
    ] = False,
    branch: Annotated[
        bool,
        typer.Option("--branch", "-b", help="Create a wakil/ingest/* branch and commit there."),
    ] = False,
    commit: Annotated[
        bool, typer.Option("--commit", "-c", help="Commit written files on the current branch.")
    ] = False,
    pr: Annotated[
        bool, typer.Option("--pr", help="Push the branch and open a PR via gh (implies -b).")
    ] = False,
) -> None:
    """Step 2: analyze a captured source and link it into the knowledge base."""
    from wakil.app.git_service import GitServiceError, start_ingest_branch
    from wakil.app.ingest_service import (
        IngestError,
        apply_enrichment,
        prepare_enrichment,
        validate_proposal,
    )
    from wakil.llm.client import ModelError, resolve_client

    root = _resolve_workspace(ctx)
    config = WorkspaceConfig.load(root)
    if pr:
        branch = True
    client = resolve_client()
    if client is None:
        console.print(
            "[red]Enrichment needs a model provider.[/red] Set [bold]ANTHROPIC_API_KEY[/bold] "
            "(or OPENAI_API_KEY + WAKIL_MODEL for an OpenAI-compatible endpoint)."
        )
        raise typer.Exit(code=1)
    try:
        with console.status(f"Analyzing source #{source_id} with {client.model}..."):
            proposal = prepare_enrichment(config, source_id, client, context=context, force=force)
    except (IngestError, ModelError) as exc:
        console.print(f"[red]Enrichment failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    print_enrichment_proposal(proposal)
    issues = validate_proposal(proposal)
    if issues:
        print_proposal_issues(issues)
        raise typer.Exit(code=1)
    if not yes and not typer.confirm("Apply this enrichment (write files, record memories)?"):
        console.print("Aborted; nothing was written.")
        raise typer.Exit(code=0)

    branch_name: str | None = None
    try:
        if branch:
            branch_name = start_ingest_branch(config, proposal.title)
            console.print(f"Created branch [bold]{branch_name}[/bold]")
        result = apply_enrichment(config, proposal)
    except (IngestError, GitServiceError) as exc:
        console.print(f"[red]Enrichment failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    print_enrichment_result(result)

    if (branch or commit) and result.files_written:
        _commit_written_files(
            config,
            result.files_written,
            proposal.title,
            proposal.summary or None,
            result.ingest_run_id,
            branch_name,
            pr,
            kind="ingest",
        )
    elif branch or commit:
        console.print("[dim]No files were written; nothing to commit.[/dim]")


_YES = Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")]
_BRANCH = Annotated[
    bool, typer.Option("--branch", "-b", help="Create a wakil/ingest/* branch and commit there.")
]
_COMMIT = Annotated[
    bool, typer.Option("--commit", "-c", help="Commit the ingested files on the current branch.")
]
_PR = Annotated[
    bool, typer.Option("--pr", help="Push the ingest branch and open a PR via gh (implies -b).")
]
_CONTEXT = Annotated[
    str | None,
    typer.Option(
        "--context",
        "-C",
        help="A few lines of context about the source (attendees, company, purpose) "
        "to guide analysis and entity linking.",
    ),
]


@ingest_app.command("transcript")
def ingest_transcript(
    ctx: typer.Context,
    file: Annotated[Path, typer.Argument(help="Transcript file (.txt, .md, or .srt).")],
    context: _CONTEXT = None,
    yes: _YES = False,
    branch: _BRANCH = False,
    commit: _COMMIT = False,
    pr: _PR = False,
) -> None:
    """Ingest a meeting or call transcript."""
    _run_ingest(
        ctx, "transcript", yes, file=file, branch=branch, commit=commit, pr=pr, context=context
    )


@ingest_app.command("text")
def ingest_text(
    ctx: typer.Context,
    file: Annotated[Path, typer.Argument(help="Text or Markdown file to ingest.")],
    context: _CONTEXT = None,
    yes: _YES = False,
    branch: _BRANCH = False,
    commit: _COMMIT = False,
    pr: _PR = False,
) -> None:
    """Ingest a plain text file, pasted note, or clipping."""
    _run_ingest(ctx, "text", yes, file=file, branch=branch, commit=commit, pr=pr, context=context)


@ingest_app.command("article")
def ingest_article(
    ctx: typer.Context,
    url: Annotated[str, typer.Argument(help="Web article URL.")],
    context: _CONTEXT = None,
    yes: _YES = False,
    branch: _BRANCH = False,
    commit: _COMMIT = False,
    pr: _PR = False,
) -> None:
    """Fetch a web article, extract its text, and ingest it."""
    _run_ingest(ctx, "article", yes, url=url, branch=branch, commit=commit, pr=pr, context=context)


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
            "--state", help="Filter by state: working|candidate|durable|rejected|archived."
        ),
    ] = None,
    memory_type: Annotated[
        str | None, typer.Option("--type", help="Filter by memory type (fact, decision, ...).")
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
        str | None, typer.Option("--type", help="Migrate only notes of this entity type.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the plan and diffs; write nothing.")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Apply every proposed fix without prompting.")
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


@app.command()
def version() -> None:
    """Print the wakil version."""
    console.print(f"wakil {wakil.__version__}")


if __name__ == "__main__":
    app()
