"""Rich console output helpers."""

from rich.console import Console
from rich.table import Table

from wakil.app.workspace_service import IndexResult, WorkspaceStatus

console = Console()


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
