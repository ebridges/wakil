"""mcp/tools.py: plain-data wrappers over the app/*_service.py functions,
tested directly (no running MCP server), same conventions as
test_ingest_service.py/test_git_service.py (FakeClient, git_kb fixture with
gh_available monkeypatched off)."""

import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from wakil.app.workspace_service import init_workspace, open_session
from wakil.config.settings import WorkspaceConfig
from wakil.mcp import tools
from wakil.mcp.proposals import ProposalCache
from wakil.storage.schema import Memory, Note, Relationship, Source, Workspace

CAPTURE_METADATA_JSON = {
    "title": "2026-07-09 Test Meeting",
    "abstract": "A short abstract of the test meeting.",
}

EXTRACTION_JSON = {
    "title": "Claims Kickoff Meeting",
    "summary": "The team agreed to prototype FNOL routing using graph memory.",
    "key_points": ["Prototype approved"],
    "memories": [
        {"type": "decision", "content": "Team will prototype FNOL routing.", "confidence": 0.9}
    ],
    "relationships": [],
    "proposed_note": {
        "path": "meetings/2026/2026-07-09-claims-kickoff.md",
        "markdown": (
            "---\ntype: meeting\ntitle: Claims Kickoff\ndate: 2026-07-09\n"
            "created: 2026-07-09\n---\n\n"
            "# Claims Kickoff\n\nAttended by Jane. See [[concepts/claims-routing.md]].\n"
        ),
    },
}

RESOLUTION_JSON = {"entities": []}


class FakeClient:
    model = "fake-model"

    def __init__(self, payloads=None):
        payloads = payloads if payloads is not None else [CAPTURE_METADATA_JSON]
        self.queue = [json.dumps(p) if isinstance(p, dict) else p for p in payloads]
        self.calls: list[tuple[str, str]] = []

    def complete(self, system, prompt, max_tokens=8192, *, cacheable_prefix=None):
        self.calls.append((system, prompt))
        assert self.queue, "FakeClient ran out of scripted responses"
        return self.queue.pop(0)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def workspace(kb_path: Path) -> WorkspaceConfig:
    init_workspace(kb_path)
    return WorkspaceConfig.load(kb_path)


@pytest.fixture
def git_kb(kb_path: Path) -> WorkspaceConfig:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=kb_path, check=True)
    _git(kb_path, "config", "user.email", "test@example.com")
    _git(kb_path, "config", "user.name", "Test User")
    _git(kb_path, "config", "commit.gpgsign", "false")
    _git(kb_path, "add", "-A")
    _git(kb_path, "commit", "-q", "-m", "seed")
    init_workspace(kb_path)
    return WorkspaceConfig.load(kb_path)


@pytest.fixture
def transcript(kb_path: Path) -> Path:
    path = kb_path / "2026-07-09-raw-meeting.txt"
    path.write_text("Jane: let's prototype FNOL routing with graph memory.\nBob: agreed.\n")
    return path


def _ignore_in_git(root: Path, name: str) -> None:
    """Keep an ad hoc raw-input file outside the tree ensure_clean_for_branch
    inspects — same convention as test_git_cli.py's git-backed ingest tests.
    The .gitignore edit itself must be committed too, or it dirties the tree."""
    gitignore = root / ".gitignore"
    gitignore.write_text((gitignore.read_text() if gitignore.exists() else "") + f"{name}\n")
    _git(root, "add", ".gitignore")
    _git(root, "commit", "-q", "-m", "ignore scratch")


@pytest.fixture(autouse=True)
def _no_gh(monkeypatch):
    """Deterministic across dev machines that do/don't have gh installed —
    same convention as test_git_service.py."""
    monkeypatch.setattr("wakil.app.git_service.gh_available", lambda: False)


# --------------------------------------------------------------------------
# Read tools


def test_status_reports_counts_and_git_state(workspace):
    result = tools.status(workspace)
    assert result["name"] == workspace.name
    assert result["note_count"] > 0
    assert "git" in result and "qmd" in result


def test_search_finds_notes(workspace):
    hits = tools.search(workspace, "graph memory")
    assert any(h["ref"] == "concepts/graph-memory.md" for h in hits)


def test_query_requires_model_provider(workspace, monkeypatch):
    monkeypatch.setattr("wakil.mcp.tools.resolve_client", lambda: None)
    with pytest.raises(tools.ToolError, match="No model provider"):
        tools.query(workspace, "what is graph memory?")


def test_query_answers_with_citations(workspace, monkeypatch):
    monkeypatch.setattr(
        "wakil.mcp.tools.resolve_client",
        lambda: FakeClient(["Graph memory relates to claims routing [1]."]),
    )
    result = tools.query(workspace, "how does graph memory relate to claims routing?")
    assert "Graph memory relates to claims routing" in result["answer"]
    assert result["citations"]


def test_memory_list_and_show_roundtrip(workspace):
    with open_session(workspace) as session:
        workspace_id = session.scalar(select(Workspace.id))
        user_id = session.execute(select(Workspace.user_id)).scalar_one()
        memory = Memory(
            workspace_id=workspace_id,
            user_id=user_id,
            memory_type="fact",
            content="Jane owns the routing design.",
            state="durable",
        )
        session.add(memory)
        session.commit()
        memory_id = memory.id

    listed = tools.memory_list(workspace, state="durable")
    assert any(m["id"] == memory_id for m in listed)

    shown = tools.memory_show(workspace, memory_id)
    assert shown["content"] == "Jane owns the routing design."

    with pytest.raises(tools.ToolError):
        tools.memory_show(workspace, memory_id + 1000)


def test_relationships_traverses_note_mentions(workspace):
    with open_session(workspace) as session:
        workspace_id = session.scalar(select(Workspace.id))
        notes = {n.path: n.id for n in session.scalars(select(Note))}
        session.add(
            Relationship(
                workspace_id=workspace_id,
                subject_note_id=notes["concepts/graph-memory.md"],
                object_note_id=notes["concepts/claims-routing.md"],
                predicate="mentions",
            )
        )
        session.commit()

    result = tools.relationships(workspace, "concepts/graph-memory.md", direction="out")
    assert any(h["path"] == "concepts/claims-routing.md" for h in result["hits"])


def test_sources_list_and_show(workspace, transcript):
    from wakil.app.ingest_service import apply_capture, prepare_capture

    proposal = prepare_capture(
        workspace, "transcript", FakeClient([CAPTURE_METADATA_JSON]), file=transcript
    )
    result = apply_capture(workspace, proposal)

    rows = tools.sources_list(workspace)
    assert any(r["id"] == result.source_id for r in rows)

    shown = tools.sources_show(workspace, result.source_id)
    assert shown["id"] == result.source_id

    with pytest.raises(tools.ToolError):
        tools.sources_show(workspace, result.source_id + 1000)


def test_git_summary_and_history(git_kb):
    result = tools.git_summary(git_kb)
    assert result["branch"] == "main"
    assert result["is_dirty"] is False

    history = tools.git_history(git_kb, "README.md")
    assert history


def test_git_summary_requires_git_repo(workspace):
    with pytest.raises(tools.ToolError, match="not a git repository"):
        tools.git_summary(workspace)


def test_skills_list_includes_builtin_skills(workspace):
    names = {row["name"] for row in tools.skills_list(workspace)}
    assert "article" in names
    assert "transcript" in names


# --------------------------------------------------------------------------
# Write tools: ingest


def test_ingest_prepare_apply_lands_capture(git_kb, transcript, monkeypatch):
    _ignore_in_git(git_kb.root_path, transcript.name)
    monkeypatch.setattr(
        "wakil.mcp.tools.resolve_client", lambda: FakeClient([CAPTURE_METADATA_JSON])
    )
    cache = ProposalCache()

    prepared = tools.ingest_prepare(git_kb, cache, "transcript", file_path=str(transcript))
    assert prepared["duplicate_of"] is None
    assert prepared["proposal_id"] is not None

    applied = tools.ingest_apply(git_kb, cache, prepared["proposal_id"])
    assert applied["source_id"] is not None
    assert applied["branch"] is not None
    assert applied["pr_url"] is None  # gh unavailable
    # Landing returns to the original branch (main), so the raw file only
    # exists on the ingest branch -- check git history, not the worktree.
    assert _git(git_kb.root_path, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    log = _git(git_kb.root_path, "log", "--all", "--format=%s")
    assert "wakil source: add" in log

    with open_session(git_kb) as session:
        source = session.get(Source, applied["source_id"])
        assert source is not None
        assert source.git_branch == applied["branch"]


def test_ingest_prepare_detects_duplicate(git_kb, transcript, monkeypatch):
    _ignore_in_git(git_kb.root_path, transcript.name)
    monkeypatch.setattr(
        "wakil.mcp.tools.resolve_client",
        lambda: FakeClient([CAPTURE_METADATA_JSON, CAPTURE_METADATA_JSON]),
    )
    cache = ProposalCache()
    first = tools.ingest_prepare(git_kb, cache, "transcript", file_path=str(transcript))
    tools.ingest_apply(git_kb, cache, first["proposal_id"])

    second = tools.ingest_prepare(git_kb, cache, "transcript", file_path=str(transcript))
    assert second["proposal_id"] is None
    assert second["duplicate_of"] is not None


def test_ingest_prepare_surfaces_capture_warnings(git_kb, monkeypatch):
    """ADR 0019 moved capture's review moment off the CLI preview and onto
    the coordinating skill, so a warning that only reaches the preview
    reaches nobody on this path."""
    path = git_kb.root_path / "linked.md"
    path.write_text("---\ntitle: [[people/jane]]\n---\n\nBody.\n", encoding="utf-8")
    _ignore_in_git(git_kb.root_path, path.name)
    monkeypatch.setattr(
        "wakil.mcp.tools.resolve_client", lambda: FakeClient([CAPTURE_METADATA_JSON])
    )

    prepared = tools.ingest_prepare(git_kb, ProposalCache(), "text", file_path=str(path))
    assert any("title" in warning for warning in prepared["warnings"])


def test_ingest_apply_unknown_proposal_id_raises(git_kb):
    with pytest.raises(tools.ToolError, match="No pending capture proposal"):
        tools.ingest_apply(git_kb, ProposalCache(), "not-a-real-id")


# --------------------------------------------------------------------------
# Write tools: enrich


def _capture(config, cache, transcript_path, monkeypatch) -> int:
    _ignore_in_git(config.root_path, transcript_path.name)
    monkeypatch.setattr(
        "wakil.mcp.tools.resolve_client", lambda: FakeClient([CAPTURE_METADATA_JSON])
    )
    prepared = tools.ingest_prepare(config, cache, "transcript", file_path=str(transcript_path))
    applied = tools.ingest_apply(config, cache, prepared["proposal_id"])
    return applied["source_id"]


def test_enrich_prepare_apply_writes_note_and_flips_pr_ready(git_kb, transcript, monkeypatch):
    cache = ProposalCache()
    source_id = _capture(git_kb, cache, transcript, monkeypatch)

    monkeypatch.setattr(
        "wakil.mcp.tools.resolve_client",
        lambda: FakeClient([EXTRACTION_JSON, RESOLUTION_JSON]),
    )
    prepared = tools.enrich_prepare(git_kb, cache, source_id)
    assert prepared["issues"] == []
    assert prepared["proposal_id"] is not None
    assert "meetings/2026/2026-07-09-claims-kickoff.md" in prepared["files_to_write"]

    applied = tools.enrich_apply(git_kb, cache, prepared["proposal_id"])
    assert applied["files_written"]
    assert "meetings/2026/2026-07-09-claims-kickoff.md" in applied["files_written"]
    assert applied["pr_url"] is None  # gh unavailable
    # Landing returns to main; the note only exists on the source's branch.
    assert _git(git_kb.root_path, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    log = _git(git_kb.root_path, "log", "--all", "--format=%s")
    assert "wakil ingest: add" in log

    with open_session(git_kb) as session:
        source = session.get(Source, source_id)
        assert source is not None
        assert source.status == "enriched"


def test_enrich_prepare_blocks_on_invalid_proposed_note(git_kb, transcript, monkeypatch):
    cache = ProposalCache()
    source_id = _capture(git_kb, cache, transcript, monkeypatch)

    bad_extraction = dict(EXTRACTION_JSON)
    bad_extraction["proposed_note"] = {
        "path": "meetings/2026/2026-07-09-claims-kickoff.md",
        "markdown": "---\ntype: not-a-real-type\n---\n\n# Claims Kickoff\n",
    }
    monkeypatch.setattr(
        "wakil.mcp.tools.resolve_client",
        lambda: FakeClient([bad_extraction, RESOLUTION_JSON]),
    )
    prepared = tools.enrich_prepare(git_kb, cache, source_id)
    assert prepared["proposal_id"] is None
    assert prepared["issues"]


def test_enrich_apply_unknown_proposal_id_raises(git_kb):
    with pytest.raises(tools.ToolError, match="No pending enrichment proposal"):
        tools.enrich_apply(git_kb, ProposalCache(), "not-a-real-id")


def test_sources_relink_archive_unarchive_over_mcp(workspace, transcript, kb_path):
    """These three are single-call writes by design (ADR 0018 amendment), so
    their errors are the only gate an agent gets — they have to arrive as
    `ToolError`, not as a raw `IngestError`."""
    from wakil.app.ingest_service import apply_capture, prepare_capture

    proposal = prepare_capture(
        workspace, "transcript", FakeClient([CAPTURE_METADATA_JSON]), file=transcript
    )
    source_id = apply_capture(workspace, proposal).source_id

    moved = kb_path / "sources" / "transcripts" / "hand-fixed.md"
    moved.write_text("---\ntype: source\n---\n\n# Fixed\n", encoding="utf-8")
    relinked = tools.sources_relink(workspace, source_id, "sources/transcripts/hand-fixed.md")
    assert relinked["raw_text_path"] == "sources/transcripts/hand-fixed.md"

    # An agent-callable tool must not be able to point a source outside the
    # workspace, or into wakil's own state.
    with pytest.raises(tools.ToolError, match="outside the knowledge base"):
        tools.sources_relink(workspace, source_id, "/etc/hosts")
    with pytest.raises(tools.ToolError):
        tools.sources_relink(workspace, source_id, "../escape.md")

    archived = tools.sources_archive(workspace, source_id, reason="wrong recording")
    assert archived["archived_at"] is not None
    assert not any(r["id"] == source_id for r in tools.sources_list(workspace))

    restored = tools.sources_unarchive(workspace, source_id)
    assert restored["archived_at"] is None

    with pytest.raises(tools.ToolError):
        tools.sources_archive(workspace, source_id + 1000)
