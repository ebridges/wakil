"""wakil's MCP server: `wakil mcp serve` (docs/adr/0018).

Binds to exactly one workspace for the life of the process (same model as
every other `wakil` command's `-w/--workspace` resolution) and registers a
small set of tools, thin wrappers over `mcp/tools.py`. Read tools return
plain data; the two flows that write Markdown (`ingest_*`, `enrich_*`) are
prepare/apply pairs backed by `mcp/proposals.py`'s in-process cache, so a
client must make an explicit second call before anything lands in the
knowledge base — the MCP analogue of the CLI's preview-then-confirm gate.

The `sources_*` maintenance tools (`relink`, `archive`, `unarchive`) are
deliberately single-call instead: they touch only operational metadata in
SQLite, never the user's Markdown, and each is reversible by another call.
A prepare/apply pair would be ceremony over a pointer update. That narrows
ADR 0018's original "no tool writes without a preview call" consequence, so
it is recorded as an amendment in the ADR itself, not just here.

Also exposes `skills/mcp-coordinator/SKILL.md` (the fast-capture coordinator
skill, docs/adr/0019) as an MCP resource so a connected client sees it with
no manual install step.
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from wakil.app.graph_service import Direction
from wakil.config.settings import WorkspaceConfig
from wakil.mcp import tools
from wakil.mcp.proposals import ProposalCache

# .../wakil/src/wakil/mcp/server.py -> repo root, four parents up. Only
# resolves in a dev/editable checkout (this project's only supported install
# shape today, per CLAUDE.md's local-first positioning) — not a published
# wheel install, which the resource read below reports clearly rather than
# crashing at import time.
_REPO_ROOT = Path(__file__).resolve().parents[3]
COORDINATOR_SKILL_PATH = _REPO_ROOT / "skills" / "mcp-coordinator" / "SKILL.md"


def _register_coordinator_resource(mcp: FastMCP) -> None:
    @mcp.resource(
        "wakil://skill/mcp-coordinator",
        name="mcp-coordinator",
        title="Fast-capture ingest/enrich coordinator skill",
        description="How to chain ingest_prepare/apply and enrich_prepare/apply for "
        "low-friction capture, and when to pause for a human instead.",
        mime_type="text/markdown",
    )
    def mcp_coordinator_skill() -> str:
        try:
            return COORDINATOR_SKILL_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            raise tools.ToolError(
                f"Could not read the coordinator skill at {COORDINATOR_SKILL_PATH}: {exc}"
            ) from exc


def _register_read_tools(mcp: FastMCP, config: WorkspaceConfig) -> None:
    @mcp.tool()
    def status() -> dict:
        """Workspace status: note/source/memory counts, git state, QMD availability."""
        return tools.status(config)

    @mcp.tool()
    def search(query: str, limit: int = 10, mode: str = "search") -> list[dict]:
        """Hybrid QMD + SQLite FTS search over notes, memories, and sources."""
        return tools.search(config, query, limit=limit, mode=mode)

    @mcp.tool()
    def query(
        question: str, limit: int = 10, mode: str = "search", include_casual: bool = False
    ) -> dict:
        """Grounded, cited answer to a question over the knowledge base."""
        return tools.query(
            config, question, limit=limit, mode=mode, include_casual=include_casual
        )

    @mcp.tool()
    def memory_list(
        state: str | None = None,
        memory_type: str | None = None,
        stance: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """List memory records, optionally filtered by lifecycle state/type/register."""
        return tools.memory_list(
            config, state=state, memory_type=memory_type, stance=stance, limit=limit
        )

    @mcp.tool()
    def memory_show(memory_id: int) -> dict:
        """Show one memory record by id."""
        return tools.memory_show(config, memory_id)

    @mcp.tool()
    def relationships(
        anchor_path: str,
        direction: Direction = "both",
        predicate: str | None = None,
        depth: int = 1,
    ) -> dict:
        """Walk Note<->Note relationship edges out from an anchor note path."""
        return tools.relationships(
            config, anchor_path, direction=direction, predicate=predicate, depth=depth
        )

    @mcp.tool()
    def sources_list(
        status: str | None = None, limit: int | None = 50, include_archived: bool = False
    ) -> list[dict]:
        """List captured sources, most recent first."""
        return tools.sources_list(
            config, status=status, limit=limit, include_archived=include_archived
        )

    @mcp.tool()
    def sources_show(source_id: int) -> dict:
        """Show one captured source by id."""
        return tools.sources_show(config, source_id)

    @mcp.tool()
    def skills_list() -> list[dict]:
        """List effective wakil skills (DAG-internal extraction skills) and their source."""
        return tools.skills_list(config)


def _register_git_tools(mcp: FastMCP, config: WorkspaceConfig) -> None:
    @mcp.tool()
    def git_summary() -> dict:
        """Current branch, uncommitted changes, recent commits, wakil branches."""
        return tools.git_summary(config)

    @mcp.tool()
    def git_history(path: str, limit: int = 10) -> list[str]:
        """Commit history for one workspace-relative file path."""
        return tools.git_history(config, path, limit=limit)


def _register_write_tools(mcp: FastMCP, config: WorkspaceConfig, cache: ProposalCache) -> None:
    @mcp.tool()
    def sources_relink(source_id: int, path: str) -> dict:
        """Point a source at its raw capture's current path after a rename.

        `path` must be inside this workspace; anything else is rejected."""
        return tools.sources_relink(config, source_id, path)

    @mcp.tool()
    def sources_archive(
        source_id: int, reason: str | None = None, superseded_by: int | None = None
    ) -> dict:
        """Retire a source without deleting it: it drops out of the default
        listing but its row, memories, and history are kept."""
        return tools.sources_archive(
            config, source_id, reason=reason, superseded_by=superseded_by
        )

    @mcp.tool()
    def sources_unarchive(source_id: int) -> dict:
        """Undo sources_archive."""
        return tools.sources_unarchive(config, source_id)

    @mcp.tool()
    def ingest_prepare(
        kind: str, file_path: str | None = None, url: str | None = None, context: str | None = None
    ) -> dict:
        """Prepare (preview) capturing a source. Call ingest_apply with the
        returned proposal_id to actually write it; nothing is written yet."""
        return tools.ingest_prepare(
            config, cache, kind, file_path=file_path, url=url, context=context
        )

    @mcp.tool()
    def ingest_apply(proposal_id: str) -> dict:
        """Write a previously prepared capture: records the source, opens a
        branch and a draft PR (or reuses the source's existing one)."""
        return tools.ingest_apply(config, cache, proposal_id)

    @mcp.tool()
    def enrich_prepare(source_id: int, context: str | None = None, force: bool = False) -> dict:
        """Prepare (preview) enrichment for a captured source: extraction +
        entity resolution. Call enrich_apply with the returned proposal_id to
        actually write anything; nothing is written yet."""
        return tools.enrich_prepare(config, cache, source_id, context=context, force=force)

    @mcp.tool()
    def enrich_apply(proposal_id: str) -> dict:
        """Write a previously prepared enrichment: writes/updates notes,
        records memories/relationships, and flips the source's PR to
        ready-for-review."""
        return tools.enrich_apply(config, cache, proposal_id)


def build_server(config: WorkspaceConfig) -> FastMCP:
    mcp = FastMCP("wakil")
    cache = ProposalCache()

    _register_coordinator_resource(mcp)
    _register_read_tools(mcp, config)
    _register_git_tools(mcp, config)
    _register_write_tools(mcp, config, cache)

    return mcp


def run_stdio(config: WorkspaceConfig) -> None:
    build_server(config).run(transport="stdio")
