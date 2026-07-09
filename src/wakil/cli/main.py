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
    print_index_result,
    print_ingest_proposal,
    print_ingest_result,
    print_query_result,
    print_search_hits,
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


def _run_ingest(
    ctx: typer.Context, kind: str, yes: bool, file: Path | None = None, url: str | None = None
) -> None:
    from wakil.app.ingest_service import IngestError, apply_ingest, prepare_ingest
    from wakil.llm.client import ModelError, resolve_client

    root = _resolve_workspace(ctx)
    config = WorkspaceConfig.load(root)
    client = resolve_client()
    if client is None:
        console.print(
            "[yellow]No model provider configured[/yellow] — ingesting without "
            "summary/memory extraction. Set ANTHROPIC_API_KEY to enable it."
        )
    try:
        with console.status("Preparing ingest..."):
            proposal = prepare_ingest(config, kind, file=file, url=url, client=client)
    except (IngestError, ModelError) as exc:
        console.print(f"[red]Ingest failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if proposal.duplicate_of is not None:
        console.print(
            f"[yellow]Already ingested[/yellow] (matches source #{proposal.duplicate_of}); "
            "nothing to do."
        )
        return

    print_ingest_proposal(proposal)
    if not yes and not typer.confirm("Write these files and record the source?"):
        console.print("Aborted; nothing was written.")
        raise typer.Exit(code=0)
    try:
        result = apply_ingest(config, proposal)
    except IngestError as exc:
        console.print(f"[red]Ingest failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    print_ingest_result(result)


@ingest_app.command("transcript")
def ingest_transcript(
    ctx: typer.Context,
    file: Annotated[Path, typer.Argument(help="Transcript file (.txt, .md, or .srt).")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Ingest a meeting or call transcript."""
    _run_ingest(ctx, "transcript", yes, file=file)


@ingest_app.command("text")
def ingest_text(
    ctx: typer.Context,
    file: Annotated[Path, typer.Argument(help="Text or Markdown file to ingest.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Ingest a plain text file, pasted note, or clipping."""
    _run_ingest(ctx, "text", yes, file=file)


@ingest_app.command("article")
def ingest_article(
    ctx: typer.Context,
    url: Annotated[str, typer.Argument(help="Web article URL.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Fetch a web article, extract its text, and ingest it."""
    _run_ingest(ctx, "article", yes, url=url)


@app.command()
def version() -> None:
    """Print the wakil version."""
    console.print(f"wakil {wakil.__version__}")


if __name__ == "__main__":
    app()
