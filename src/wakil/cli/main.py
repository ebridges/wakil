"""wakil CLI entry point."""

from pathlib import Path
from typing import Annotated

import typer

import wakil
from wakil.app.workspace_service import get_status, init_workspace
from wakil.config.settings import WorkspaceConfig, find_workspace_root
from wakil.ui.console import (
    console,
    print_index_result,
    print_ingest_proposal,
    print_ingest_result,
    print_query_result,
    print_search_hits,
    print_status,
)

app = typer.Typer(
    name="wakil",
    help="Local-first agent for a personal Markdown knowledge base.",
    no_args_is_help=True,
)
ingest_app = typer.Typer(help="Ingest raw sources into the knowledge base.", no_args_is_help=True)
app.add_typer(ingest_app, name="ingest")


def _require_workspace(path: Path) -> Path:
    root = find_workspace_root(path)
    if root is None:
        console.print(
            "[red]No wakil workspace found.[/red] Run [bold]wakil init <path>[/bold] first."
        )
        raise typer.Exit(code=1)
    return root


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
    console.print(f"Workspace [bold]{status.config.name}[/bold] ready at {status.config.root_path}")
    print_index_result(index_result)
    print_status(status)


@app.command()
def status(
    path: Annotated[
        Path, typer.Option("--path", help="Path inside the workspace (defaults to cwd).")
    ] = Path("."),
) -> None:
    """Show workspace status: notes, git state, QMD availability."""
    root = find_workspace_root(path)
    if root is None:
        console.print(
            "[red]No wakil workspace found.[/red] Run [bold]wakil init <path>[/bold] first."
        )
        raise typer.Exit(code=1)
    print_status(get_status(root))


@app.command()
def index(
    path: Annotated[
        Path, typer.Option("--path", help="Path inside the workspace (defaults to cwd).")
    ] = Path("."),
) -> None:
    """Re-index Markdown files into the workspace database."""
    root = find_workspace_root(path)
    if root is None:
        console.print(
            "[red]No wakil workspace found.[/red] Run [bold]wakil init <path>[/bold] first."
        )
        raise typer.Exit(code=1)
    _, index_result = init_workspace(root)
    print_index_result(index_result)


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Free-text search query.")],
    path: Annotated[
        Path, typer.Option("--path", help="Path inside the workspace (defaults to cwd).")
    ] = Path("."),
    limit: Annotated[int, typer.Option("--limit", help="Max results per engine.")] = 10,
    mode: Annotated[
        str,
        typer.Option("--mode", help="QMD mode: search (BM25), vsearch (vector), query (hybrid)."),
    ] = "search",
) -> None:
    """Search the knowledge base via QMD plus local FTS indexes."""
    from wakil.app.search_service import search_workspace
    from wakil.app.workspace_service import open_session

    root = _require_workspace(path)
    config = WorkspaceConfig.load(root)
    with open_session(config) as session:
        hits = search_workspace(session, config, query, limit=limit, mode=mode)
    print_search_hits(hits, query)
    if not config.qmd_enabled:
        console.print("[dim]QMD not detected; results are from local indexes only.[/dim]")


@app.command()
def query(
    question: Annotated[str, typer.Argument(help="Question to answer from the knowledge base.")],
    path: Annotated[
        Path, typer.Option("--path", help="Path inside the workspace (defaults to cwd).")
    ] = Path("."),
    limit: Annotated[int, typer.Option("--limit", help="Max search hits to consider.")] = 10,
    mode: Annotated[
        str,
        typer.Option("--mode", help="QMD mode: search (BM25), vsearch (vector), query (hybrid)."),
    ] = "search",
) -> None:
    """Answer a question with grounded citations from the knowledge base."""
    from wakil.app.query_service import run_query
    from wakil.llm.client import ModelError, resolve_client

    root = _require_workspace(path)
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
    kind: str, path: Path, yes: bool, file: Path | None = None, url: str | None = None
) -> None:
    from wakil.app.ingest_service import IngestError, apply_ingest, prepare_ingest
    from wakil.llm.client import ModelError, resolve_client

    root = _require_workspace(path)
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
    file: Annotated[Path, typer.Argument(help="Transcript file (.txt, .md, or .srt).")],
    path: Annotated[
        Path, typer.Option("--path", help="Path inside the workspace (defaults to cwd).")
    ] = Path("."),
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Ingest a meeting or call transcript."""
    _run_ingest("transcript", path, yes, file=file)


@ingest_app.command("text")
def ingest_text(
    file: Annotated[Path, typer.Argument(help="Text or Markdown file to ingest.")],
    path: Annotated[
        Path, typer.Option("--path", help="Path inside the workspace (defaults to cwd).")
    ] = Path("."),
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Ingest a plain text file, pasted note, or clipping."""
    _run_ingest("text", path, yes, file=file)


@ingest_app.command("article")
def ingest_article(
    url: Annotated[str, typer.Argument(help="Web article URL.")],
    path: Annotated[
        Path, typer.Option("--path", help="Path inside the workspace (defaults to cwd).")
    ] = Path("."),
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Fetch a web article, extract its text, and ingest it."""
    _run_ingest("article", path, yes, url=url)


@app.command()
def version() -> None:
    """Print the wakil version."""
    console.print(f"wakil {wakil.__version__}")


if __name__ == "__main__":
    app()
