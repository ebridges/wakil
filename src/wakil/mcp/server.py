"""wakil's MCP server: `wakil mcp serve` (docs/adr/0018).

Binds to exactly one workspace for the life of the process (same model as
every other `wakil` command's `-w/--workspace` resolution) and registers a
small set of tools, thin wrappers over `mcp/tools.py`. Read tools return
plain data; the two write flows (`ingest_*`, `enrich_*`) are prepare/apply
pairs backed by `mcp/proposals.py`'s in-process cache, so a client must make
an explicit second call to actually write anything — the MCP analogue of
the CLI's preview-then-confirm gate.

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


def build_server(config: WorkspaceConfig) -> FastMCP:
    mcp = FastMCP("wakil")
    cache = ProposalCache()

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
    def sources_list(status: str | None = None, limit: int | None = 50) -> list[dict]:
        """List captured sources, most recent first."""
        return tools.sources_list(config, status=status, limit=limit)

    @mcp.tool()
    def sources_show(source_id: int) -> dict:
        """Show one captured source by id."""
        return tools.sources_show(config, source_id)

    @mcp.tool()
    def git_summary() -> dict:
        """Current branch, uncommitted changes, recent commits, wakil branches."""
        return tools.git_summary(config)

    @mcp.tool()
    def git_history(path: str, limit: int = 10) -> list[str]:
        """Commit history for one workspace-relative file path."""
        return tools.git_history(config, path, limit=limit)

    @mcp.tool()
    def skills_list() -> list[dict]:
        """List effective wakil skills (DAG-internal extraction skills) and their source."""
        return tools.skills_list(config)

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

    return mcp


def run_stdio(config: WorkspaceConfig) -> None:
    build_server(config).run(transport="stdio")
