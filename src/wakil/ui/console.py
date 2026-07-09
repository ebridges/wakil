"""Rich console output helpers."""

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from wakil.app.query_service import QueryResult
from wakil.app.search_service import SearchHit
from wakil.app.workspace_service import IndexResult, WorkspaceStatus

console = Console()

_KIND_STYLES = {"note": "green", "memory": "magenta", "source": "yellow"}


def print_search_hits(hits: list[SearchHit], query: str) -> None:
    if not hits:
        console.print(f"No results for [bold]{query}[/bold].")
        return
    table = Table(title=f"Search: {query}")
    table.add_column("Kind", style="bold")
    table.add_column("Ref", overflow="fold")
    table.add_column("Title")
    table.add_column("Snippet", overflow="fold")
    table.add_column("Via", style="dim")
    for hit in hits:
        style = _KIND_STYLES.get(hit.kind, "white")
        table.add_row(f"[{style}]{hit.kind}[/{style}]", hit.ref, hit.title, hit.snippet, hit.engine)
    console.print(table)


def print_query_result(result: QueryResult) -> None:
    console.print(Panel(Markdown(result.answer), title="Answer", border_style="cyan"))
    if result.contexts:
        console.print("[bold]Citations:[/bold]")
        for i, block in enumerate(result.contexts, start=1):
            style = _KIND_STYLES.get(block.kind, "white")
            console.print(f"  [{i}] [{style}]{block.kind}[/{style}] {block.ref}")


def print_status(status: WorkspaceStatus) -> None:
    table = Table(title=f"wakil workspace: {status.config.name}", show_header=False)
    table.add_column("Field", style="bold cyan")
    table.add_column("Value")

    table.add_row("Root", str(status.config.root_path))
    table.add_row("Notes indexed", str(status.note_count))
    table.add_row("Sources", str(status.source_count))
    table.add_row("Memories", str(status.memory_count))

    if status.git.is_repo:
        dirty = "[yellow]dirty[/yellow]" if status.git.is_dirty else "[green]clean[/green]"
        table.add_row("Git", f"branch [bold]{status.git.branch}[/bold] ({dirty})")
        if status.git.remote_url:
            table.add_row("Git remote", status.git.remote_url)
    else:
        table.add_row("Git", "[dim]not a git repository[/dim]")

    if status.qmd.available:
        qmd_value = f"[green]available[/green] {status.qmd.version or ''}".strip()
    else:
        qmd_value = "[dim]not found on PATH[/dim]"
    table.add_row("QMD", qmd_value)
    if status.qmd.workspace_scripts:
        table.add_row("QMD scripts", ", ".join(status.qmd.workspace_scripts))

    if status.special_files:
        table.add_row("Workspace context", ", ".join(status.special_files))

    console.print(table)

    if status.git.recent_commits:
        console.print("[bold]Recent commits:[/bold]")
        for line in status.git.recent_commits:
            console.print(f"  [dim]{line}[/dim]")


def print_index_result(result: IndexResult) -> None:
    console.print(
        f"Indexed [bold]{result.total}[/bold] notes "
        f"([green]{result.added} added[/green], "
        f"[yellow]{result.updated} updated[/yellow], "
        f"{result.unchanged} unchanged, "
        f"[red]{result.removed} removed[/red])"
    )
