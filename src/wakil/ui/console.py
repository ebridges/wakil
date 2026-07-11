"""Rich console output helpers."""

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from wakil.app.ingest_service import (
    CaptureProposal,
    CaptureResult,
    EnrichmentProposal,
    EnrichmentResult,
    ProposedFile,
)
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


def _print_file_preview(proposed: ProposedFile) -> None:
    preview = proposed.content[:1500]
    if len(proposed.content) > 1500:
        preview += "\n…"
    console.print(
        Panel(
            Syntax(preview, "markdown", background_color="default"),
            title=f"NEW {proposed.path}",
            border_style="green",
        )
    )


def print_capture_proposal(proposal: CaptureProposal) -> None:
    header = f"[bold]{proposal.title}[/bold]\n[dim]{proposal.source_type}: {proposal.origin}[/dim]"
    if proposal.meeting_date:
        header += f"\n[dim]Meeting date: {proposal.meeting_date}[/dim]"
    if proposal.context:
        header += f"\n[dim]Context: {proposal.context}[/dim]"
    console.print(Panel(header, title="Capture preview", border_style="cyan"))
    console.print("[bold]Raw capture:[/bold]")
    _print_file_preview(proposal.raw_file)


def print_capture_result(result: CaptureResult) -> None:
    console.print(
        f"Captured source [bold]#{result.source_id}[/bold]: [green]+ {result.raw_file_path}[/green]"
    )
    console.print(
        f"[dim]Analyze and link it into the knowledge base with "
        f"`wakil enrich {result.source_id}`.[/dim]"
    )


def print_enrichment_proposal(proposal: EnrichmentProposal) -> None:
    header = f"[bold]{proposal.title}[/bold]\n[dim]source #{proposal.source_id}[/dim]"
    if proposal.context:
        header += f"\n[dim]Context: {proposal.context}[/dim]"
    console.print(Panel(header, title="Enrichment preview", border_style="cyan"))
    if proposal.summary:
        console.print(Panel(Markdown(proposal.summary), title="Summary"))
    if proposal.key_points:
        console.print("[bold]Key points:[/bold]")
        for point in proposal.key_points:
            console.print(f"  • {point}")
    if proposal.related_notes:
        console.print("[bold]Related notes:[/bold]")
        for hit in proposal.related_notes:
            console.print(f"  [green]{hit.ref}[/green] {hit.title}")
    if proposal.memories:
        table = Table(title="Candidate memories")
        table.add_column("#", style="dim")
        table.add_column("Type", style="magenta")
        table.add_column("Content", overflow="fold")
        table.add_column("Conf", justify="right")
        for i, memory in enumerate(proposal.memories):
            conf = f"{memory.confidence:.2f}" if memory.confidence is not None else "-"
            table.add_row(str(i), memory.memory_type, memory.content, conf)
        console.print(table)
    if proposal.relationships:
        console.print("[bold]Candidate relationships:[/bold]")
        for rel in proposal.relationships:
            console.print(f"  [{rel.subject_index}] --{rel.predicate}--> [{rel.object_index}]")
    if proposal.proposed_note is not None:
        console.print("[bold]Proposed note:[/bold]")
        _print_file_preview(proposal.proposed_note)


def print_enrichment_result(result: EnrichmentResult) -> None:
    console.print(
        f"Enriched source [bold]#{result.source_id}[/bold]: "
        f"{len(result.files_written)} file(s) written, "
        f"[magenta]{result.memories_created}[/magenta] candidate memories, "
        f"{result.relationships_created} relationships."
    )
    for path in result.files_written:
        console.print(f"  [green]+ {path}[/green]")
    console.print(
        "[dim]Review files with git diff/status; review memories with "
        "`wakil memory list --state candidate`.[/dim]"
    )


_STATE_STYLES = {
    "durable": "green",
    "candidate": "yellow",
    "working": "cyan",
    "archived": "dim",
    "rejected": "red",
}


def _styled_state(state: str) -> str:
    style = _STATE_STYLES.get(state, "white")
    return f"[{style}]{state}[/{style}]"


def print_memories(memories: list) -> None:
    if not memories:
        console.print("No memories match.")
        return
    table = Table(title="Memories")
    table.add_column("ID", style="bold", justify="right")
    table.add_column("State")
    table.add_column("Type", style="magenta")
    table.add_column("Content", overflow="fold", max_width=70)
    table.add_column("Conf", justify="right")
    table.add_column("Source", style="dim")
    for memory in memories:
        conf = f"{memory.confidence:.2f}" if memory.confidence is not None else "-"
        source = f"source:{memory.source_id}" if memory.source_id else "-"
        table.add_row(
            str(memory.id),
            _styled_state(memory.state),
            memory.memory_type,
            memory.content,
            conf,
            source,
        )
    console.print(table)
    console.print(
        "[dim]wakil memory promote|reject|archive <id...> to change states; "
        "wakil memory show <id> for detail.[/dim]"
    )


def print_memory_detail(memory) -> None:
    lines = [
        f"[bold]State:[/bold] {_styled_state(memory.state)}",
        f"[bold]Type:[/bold] {memory.memory_type}",
        f"[bold]Confidence:[/bold] {memory.confidence if memory.confidence is not None else '-'}",
        f"[bold]Source:[/bold] {f'source:{memory.source_id}' if memory.source_id else '-'}",
        f"[bold]Created:[/bold] {memory.created_at}",
        f"[bold]Last seen:[/bold] {memory.last_seen_at or '-'}",
        "",
        memory.content,
    ]
    console.print(Panel("\n".join(lines), title=f"Memory #{memory.id}", border_style="magenta"))


def print_transitions(results: list) -> None:
    for result in results:
        console.print(
            f"Memory [bold]#{result.memory_id}[/bold]: "
            f"{_styled_state(result.old_state)} → {_styled_state(result.new_state)}"
        )


def print_index_result(result: IndexResult) -> None:
    console.print(
        f"Indexed [bold]{result.total}[/bold] notes "
        f"([green]{result.added} added[/green], "
        f"[yellow]{result.updated} updated[/yellow], "
        f"{result.unchanged} unchanged, "
        f"[red]{result.removed} removed[/red])"
    )
