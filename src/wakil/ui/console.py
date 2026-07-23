"""Rich console output helpers."""

import difflib

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from wakil.app.graph_service import TraversalResult
from wakil.app.ingest_service import (
    AbstractBackfillItem,
    CaptureProposal,
    CaptureResult,
    EnrichmentProposal,
    EnrichmentResult,
    EntityUpdate,
    ProposedFile,
    slugify,
)
from wakil.app.qmd_service import CollectionPlan
from wakil.app.query_service import QueryResult
from wakil.app.search_service import SearchHit
from wakil.app.workspace_service import IndexResult, WorkspaceStatus
from wakil.integrations.qmd import QmdCollection
from wakil.skills.lint import LintFinding
from wakil.skills.models import ResolvedSkill, RootIssue, SkillRoot

console = Console()

_KIND_STYLES = {"note": "green", "memory": "magenta", "source": "yellow"}
_SKILL_SOURCE_STYLES = {
    "override": "cyan",
    "kb-local": "green",
    "user": "yellow",
    "builtin": "dim",
    "error": "red",
}


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
        table.add_row(
            f"[{style}]{hit.kind}[/{style}]",
            hit.ref,
            hit.title,
            hit.snippet,
            hit.engine,
        )
    console.print(table)


def print_qmd_collections(collections: list[QmdCollection]) -> None:
    if not collections:
        console.print(
            "No QMD collections registered. Run [bold]wakil qmd sync[/bold] to propose some."
        )
        return
    table = Table(title="QMD collections")
    table.add_column("Name", style="bold")
    table.add_column("Path", overflow="fold")
    table.add_column("Pattern", style="dim")
    for collection in collections:
        table.add_row(collection.name, str(collection.path), collection.pattern)
    console.print(table)


def print_qmd_collection_plan(plans: list[CollectionPlan]) -> None:
    if not plans:
        console.print("No new collections to propose — everything already registered.")
        return
    table = Table(title="Proposed QMD collections")
    table.add_column("Name", style="bold")
    table.add_column("Path", overflow="fold")
    table.add_column("Pattern", style="dim")
    for plan in plans:
        table.add_row(plan.name, plan.path, plan.pattern)
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
    if status.qmd.available:
        if status.qmd.project_index:
            table.add_row("QMD index", "[green]workspace-scoped[/green]")
        else:
            table.add_row("QMD index", "[dim]not yet created (run 'wakil qmd sync')[/dim]")

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


def _print_entity_update(update: EntityUpdate) -> None:
    diff_lines = list(
        difflib.unified_diff(
            update.old_content.splitlines(keepends=True),
            update.new_content.splitlines(keepends=True),
            fromfile=f"{update.target_note_path} (current)",
            tofile=f"{update.target_note_path} (proposed)",
        )
    )
    diff_text = "".join(diff_lines) or "(no textual difference)"
    if len(diff_text) > 2000:
        diff_text = diff_text[:2000] + "\n…"
    console.print(
        Panel(
            Syntax(diff_text, "diff", background_color="default"),
            title=f"UPDATE {update.target_note_path}",
            border_style="yellow",
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


def print_abstract_backfill_plan(items: list[AbstractBackfillItem]) -> None:
    if not items:
        console.print("[green]Every source already has an abstract — nothing to backfill.[/green]")
        return
    table = Table(title="Abstract backfill plan")
    table.add_column("Source", justify="right")
    table.add_column("Path", overflow="fold")
    table.add_column("Title", overflow="fold")
    table.add_column("Abstract", overflow="fold")
    for item in items:
        abstract = item.abstract if len(item.abstract) <= 80 else item.abstract[:77] + "..."
        table.add_row(f"#{item.source_id}", item.raw_text_path, item.title, abstract)
    console.print(table)


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
    if proposal.entity_resolutions:
        stub_by_slug = {stub.path.rsplit("/", 1)[-1]: stub.path for stub in proposal.stub_entities}
        table = Table(title="Entity resolution")
        table.add_column("Entity", style="bold")
        table.add_column("Type")
        table.add_column("Action")
        table.add_column("Target", overflow="fold")
        table.add_column("Conf", justify="right")
        for res in proposal.entity_resolutions:
            style = _ACTION_STYLES.get(res.action, "white")
            if res.action == "update":
                target = res.target_note_path or "-"
            elif res.action == "create":
                target = stub_by_slug.get(f"{slugify(res.name)}.md", "-")
            else:
                target = "-"
            conf = f"{res.confidence:.2f}" if res.confidence is not None else "-"
            table.add_row(
                res.name,
                res.entity_type,
                f"[{style}]{res.action}[/{style}]",
                target,
                conf,
            )
        console.print(table)
    if proposal.stub_entities:
        console.print(f"[bold]New entity pages ({len(proposal.stub_entities)}):[/bold]")
        for stub in proposal.stub_entities:
            _print_file_preview(stub)
    if proposal.entity_updates:
        console.print(f"[bold]Updates to existing pages ({len(proposal.entity_updates)}):[/bold]")
        for update in proposal.entity_updates:
            _print_entity_update(update)
    if proposal.proposed_note is not None:
        console.print("[bold]Proposed note:[/bold]")
        _print_file_preview(proposal.proposed_note)
    for warning in proposal.warnings:
        # Warnings can quote wikilinks (`[[path|display]]`) or other
        # bracket-heavy content a correction touched — escape it so Rich's
        # markup parser doesn't try to read it as style tags and mangle it.
        console.print(f"[yellow]warning:[/yellow] {escape(warning)}")


_ACTION_STYLES = {"create": "green", "update": "cyan", "skip": "dim"}


def print_proposal_issues(issues) -> None:
    console.print("[red bold]Proposal failed validation — it cannot be applied:[/red bold]")
    for issue in issues:
        console.print(f"  [red]✗[/red] {issue}")
    console.print(
        "[dim]Nothing was written. Fix the gap (or re-run enrichment) and try again.[/dim]"
    )


def print_enrichment_result(result: EnrichmentResult) -> None:
    console.print(
        f"Enriched source [bold]#{result.source_id}[/bold]: "
        f"{len(result.files_written)} file(s) written, "
        f"[magenta]{result.memories_created}[/magenta] candidate memories, "
        f"{result.relationships_created} relationships."
    )
    for path in result.files_written:
        console.print(f"  [green]+ {path}[/green]")
    for skipped in result.stale_updates_skipped:
        console.print(f"  [yellow]skipped:[/yellow] {escape(skipped)}")
    console.print(
        "[dim]Review files with git diff/status; review memories with "
        "`wakil memory list --state candidate`.[/dim]"
    )


def print_migration_plan(plan) -> None:
    if plan.total_files == 0:
        console.print("[green]All indexed notes already conform — nothing to migrate.[/green]")
    else:
        table = Table(title="Schema migration plan (cheap tier)")
        table.add_column("Type", style="bold")
        table.add_column("Files", justify="right")
        table.add_column("Fixes", overflow="fold")
        for entity_type, proposals in sorted(plan.by_type.items()):
            fix_kinds: dict[str, int] = {}
            for proposal in proposals:
                for fix in proposal.fixes:
                    fix_kinds[fix] = fix_kinds.get(fix, 0) + 1
            summary = "\n".join(f"{count}× {fix}" for fix, count in sorted(fix_kinds.items()))
            table.add_row(entity_type, str(len(proposals)), summary)
        console.print(table)
    for note in plan.skipped:
        console.print(f"[yellow]skipped:[/yellow] {note}")


def print_migration_diffs(proposals) -> None:
    for proposal in proposals:
        console.print(
            Panel(
                Syntax(proposal.diff(), "diff", background_color="default"),
                title=proposal.path,
                border_style="cyan",
            )
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
    table.add_column("Register", style="dim")
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
            memory.stance or "-",
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
        f"[bold]Register:[/bold] {memory.stance or '-'}",
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


def print_traversal_result(result: TraversalResult) -> None:
    """Render a graph traversal as a table, one row per reachable note."""
    anchor_label = result.anchor_path
    if result.anchor_title and result.anchor_title != result.anchor_path:
        anchor_label = f"{result.anchor_path}  ({result.anchor_title})"
    filters: list[str] = [f"direction={result.direction}", f"depth={result.depth}"]
    if result.predicate:
        filters.append(f"predicate={result.predicate}")
    if not result.hits:
        console.print(
            f"No related notes reachable from [bold]{escape(anchor_label)}[/bold] "
            f"({', '.join(filters)})."
        )
        return
    table = Table(title=f"Relationships: {anchor_label}  [{', '.join(filters)}]")
    table.add_column("Depth", style="bold", justify="right")
    table.add_column("Dir", style="cyan", justify="center")
    table.add_column("Predicate", style="magenta")
    table.add_column("Path", overflow="fold")
    table.add_column("Title", overflow="fold")
    for hit in result.hits:
        table.add_row(
            str(hit.depth),
            hit.direction,
            hit.via_predicate,
            hit.path,
            hit.title or "",
        )
    console.print(table)


def print_index_result(result: IndexResult) -> None:
    console.print(
        f"Indexed [bold]{result.total}[/bold] notes "
        f"([green]{result.added} added[/green], "
        f"[yellow]{result.updated} updated[/yellow], "
        f"{result.unchanged} unchanged, "
        f"[red]{result.removed} removed[/red])"
    )


def print_root_issues(issues: list[RootIssue], *, prefix: str = "Warning") -> None:
    for issue in issues:
        style = "red" if prefix == "Error" else "yellow"
        console.print(f"[{style}]{prefix}:[/{style}] {issue.reason} — {issue.message}")


def print_skill_description(skill: ResolvedSkill) -> None:
    table = Table(title="Skill description")
    table.add_column(skill.name, style="bold")
    table.add_row(skill.metadata.description)
    console.print(table)


def print_skill_list(rows: list[tuple[str, str, str]]) -> None:
    """rows: (name, source, detail) — detail is the skill directory on success,
    or an error description when source == "error"."""
    if not rows:
        console.print("No skills found.")
        return
    table = Table(title="Skills")
    table.add_column("Name", style="bold")
    table.add_column("Source")
    table.add_column("Detail", overflow="fold")
    for name, source, detail in rows:
        style = _SKILL_SOURCE_STYLES.get(source, "white")
        table.add_row(name, f"[{style}]{source}[/{style}]", detail)
    console.print(table)


def print_schema_list(rows: list[tuple[str, str, str]]) -> None:
    """rows: (type, source, path) — the entity type, which root won it, and where."""
    if not rows:
        console.print("No entity schemas found.")
        return
    table = Table(title="Entity schemas")
    table.add_column("Type", style="bold")
    table.add_column("Source")
    table.add_column("Root", overflow="fold")
    for entity_type, source, path in rows:
        style = _SKILL_SOURCE_STYLES.get(source, "white")
        table.add_row(entity_type, f"[{style}]{source}[/{style}]", path)
    console.print(table)


def print_schema_which(entity_type: str, source: str, path: str) -> None:
    style = _SKILL_SOURCE_STYLES.get(source, "white")
    console.print(f"[bold]{entity_type}[/bold]  [{style}]{source}[/{style}]  {path}")


def print_skill_which(
    resolved: ResolvedSkill,
    *,
    verbose: bool,
    roots: list[SkillRoot] | None = None,
    shadowed: list[SkillRoot] | None = None,
) -> None:
    console.print(str(resolved.manifest), soft_wrap=True, highlight=False)
    if not verbose:
        return
    console.print(f"[dim]source: {resolved.source}[/dim]")
    if roots:
        console.print("[bold]Search roots:[/bold]")
        for root in roots:
            style = _SKILL_SOURCE_STYLES.get(root.source, "white")
            marker = " [green](selected)[/green]" if root.path == resolved.root else ""
            console.print(
                f"  [{style}]{root.source}[/{style}] {root.path}{marker}",
                soft_wrap=True,
                highlight=False,
            )
    if shadowed:
        console.print("[bold]Shadowed matches:[/bold]")
        for i, root in enumerate(shadowed):
            style = _SKILL_SOURCE_STYLES.get(root.source, "white")
            marker = " [green](winner)[/green]" if i == 0 else ""
            skill_dir = root.path / resolved.name
            console.print(
                f"  [{style}]{root.source}[/{style}] {skill_dir}{marker}",
                soft_wrap=True,
                highlight=False,
            )


def print_skill_validation(
    root_issues: list[RootIssue],
    results: list[tuple[str, bool, str]],
) -> None:
    """results: (name, ok, detail) — detail is "source: directory" on success,
    or "reason: message" on failure."""
    if root_issues:
        print_root_issues(root_issues, prefix="Error")
    if not results:
        console.print("No skills to validate.")
    else:
        table = Table(title="Skill validation")
        table.add_column("Name", style="bold")
        table.add_column("Status")
        table.add_column("Detail", overflow="fold")
        for name, ok, detail in results:
            status = "[green]ok[/green]" if ok else "[red]invalid[/red]"
            table.add_row(name, status, detail)
        console.print(table)
    failures = sum(1 for _, ok, _ in results if not ok)
    total_issues = len(root_issues) + failures
    if total_issues == 0:
        console.print("[green]All skills valid.[/green]")
    else:
        console.print(f"[red]{total_issues} issue(s) found.[/red]")


def print_skill_lint(findings: list[LintFinding]) -> None:
    """Content-quality lint findings, most-affected skills grouped together."""
    if not findings:
        console.print("[green]No lint findings.[/green]")
        return
    table = Table(title="Skill lint")
    table.add_column("Skill", style="bold")
    table.add_column("Check", style="yellow")
    table.add_column("Message", overflow="fold")
    for finding in findings:
        table.add_row(finding.skill, finding.check, finding.message)
    console.print(table)
    console.print(f"[red]{len(findings)} finding(s).[/red]")
