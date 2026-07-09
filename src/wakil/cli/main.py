"""wakil CLI entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

import wakil
from wakil.app.workspace_service import get_status, init_workspace
from wakil.config.settings import find_workspace_root
from wakil.ui.console import console, print_index_result, print_status

app = typer.Typer(
    name="wakil",
    help="Local-first agent for a personal Markdown knowledge base.",
    no_args_is_help=True,
)


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
def version() -> None:
    """Print the wakil version."""
    console.print(f"wakil {wakil.__version__}")


if __name__ == "__main__":
    app()
