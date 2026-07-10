"""Ingest raw sources into the knowledge base.

Two-phase flow so the CLI can show a reviewable preview between them:

    prepare_ingest()  gather text, dedupe, search related notes, run the
                      model, build proposed file writes -- touches nothing
    apply_ingest()    write files, record source/memories/relationships,
                      re-index notes

Files are only ever created (never overwritten), and DB rows are only
written in apply, so declining the preview leaves no trace.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml
from sqlalchemy import select

from wakil.app.search_service import SearchHit, search_workspace
from wakil.app.workspace_service import index_notes, open_session
from wakil.config.settings import WorkspaceConfig
from wakil.integrations.web import fetch_article
from wakil.llm.client import ModelClient
from wakil.llm.prompts import INGEST_SYSTEM_PROMPT, build_ingest_prompt, parse_ingest_response
from wakil.storage.schema import IngestRun, Memory, Relationship, Source, User, Workspace, utcnow

MAX_SOURCE_CHARS = 24_000
RELATED_NOTE_LIMIT = 5
GUIDE_MAX_CHARS = 4_000

_SRT_INDEX_RE = re.compile(r"^\d+$")
_SRT_TIMING_RE = re.compile(r"^\d{2}:\d{2}:\d{2}[,.]\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}[,.]\d{3}")

RAW_DIRS = {
    "transcript": "transcripts",
    "article": "articles",
    "text": "clippings",
}


class IngestError(RuntimeError):
    pass


@dataclass
class ProposedFile:
    path: str  # workspace-relative
    content: str


@dataclass
class CandidateMemory:
    memory_type: str
    content: str
    confidence: float | None = None


@dataclass
class CandidateRelationship:
    subject_index: int
    predicate: str
    object_index: int


@dataclass
class IngestProposal:
    source_type: str
    origin: str
    title: str
    text: str
    content_hash: str
    raw_file: ProposedFile
    context: str | None = None  # user-supplied context (attendees, company, ...)
    summary: str = ""
    key_points: list[str] = field(default_factory=list)
    memories: list[CandidateMemory] = field(default_factory=list)
    relationships: list[CandidateRelationship] = field(default_factory=list)
    proposed_note: ProposedFile | None = None
    related_notes: list[SearchHit] = field(default_factory=list)
    duplicate_of: int | None = None
    model: str | None = None


@dataclass
class IngestResult:
    source_id: int
    ingest_run_id: int
    files_written: list[str]
    memories_created: int
    relationships_created: int


def prepare_ingest(
    config: WorkspaceConfig,
    kind: str,
    *,
    file: Path | None = None,
    url: str | None = None,
    client: ModelClient | None = None,
    context: str | None = None,
) -> IngestProposal:
    if kind in ("transcript", "text"):
        if file is None:
            raise IngestError(f"{kind} ingest needs a file path")
        try:
            raw = file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise IngestError(f"Could not read {file}: {exc}") from exc
        text = strip_srt(raw) if file.suffix.lower() == ".srt" else raw
        if kind == "transcript":
            text = clean_transcript(text)
        origin = str(file)
        title = file.stem.replace("-", " ").replace("_", " ").strip() or file.name
    elif kind == "article":
        if url is None:
            raise IngestError("article ingest needs a URL")
        article = fetch_article(url)
        text = article.text
        origin = url
        title = article.title
    else:
        raise IngestError(f"unknown ingest kind: {kind}")

    if not text.strip():
        raise IngestError("Source contains no text")

    content_hash = hashlib.sha256(text.encode()).hexdigest()
    proposal = IngestProposal(
        source_type=kind,
        origin=origin,
        title=title,
        text=text,
        content_hash=content_hash,
        context=context,
        raw_file=ProposedFile(path="", content=""),
    )

    with open_session(config) as session:
        existing = session.scalar(select(Source.id).where(Source.content_hash == content_hash))
        if existing is not None:
            proposal.duplicate_of = existing
            return proposal
        # Search with the user-supplied context (attendees, company) and the
        # opening of the document, not just the title, so entity notes
        # (people/, companies/, ...) and prior meetings surface as candidates
        # for the model to link.
        related_query = " ".join(filter(None, [title, context, text[:300]]))
        proposal.related_notes = [
            hit
            for hit in search_workspace(
                config=config, session=session, query=related_query, limit=RELATED_NOTE_LIMIT
            )
            if hit.kind == "note"
        ]

    if client is not None:
        _enrich_with_model(config, proposal, client)

    proposal.raw_file = _build_raw_file(config, proposal)
    if proposal.proposed_note is not None:
        proposal.proposed_note = _sanitize_note(config, proposal)
    return proposal


def apply_ingest(config: WorkspaceConfig, proposal: IngestProposal) -> IngestResult:
    if proposal.duplicate_of is not None:
        raise IngestError(f"Source already ingested (source id {proposal.duplicate_of})")

    files_written: list[str] = []
    for proposed in filter(None, [proposal.raw_file, proposal.proposed_note]):
        target = config.root_path / proposed.path
        if target.exists():
            raise IngestError(f"Refusing to overwrite existing file: {proposed.path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(proposed.content, encoding="utf-8")
        files_written.append(proposed.path)

    with open_session(config) as session:
        workspace_id = session.scalar(
            select(Workspace.id).where(Workspace.root_path == str(config.root_path))
        )
        user_id = session.scalar(select(User.id))
        if workspace_id is None or user_id is None:
            raise IngestError("Workspace database is not initialized; run `wakil init` first.")

        source = Source(
            workspace_id=workspace_id,
            source_type=proposal.source_type,
            title=proposal.title,
            origin=proposal.origin,
            retrieved_at=utcnow(),
            content_hash=proposal.content_hash,
            raw_text_path=proposal.raw_file.path,
            status="ingested",
            metadata_json=json.dumps(
                {
                    key: value
                    for key, value in (
                        ("summary", proposal.summary),
                        ("context", proposal.context),
                    )
                    if value
                }
            ),
        )
        session.add(source)
        session.flush()

        memory_rows: list[Memory] = []
        for candidate in proposal.memories:
            memory = Memory(
                workspace_id=workspace_id,
                user_id=user_id,
                memory_type=candidate.memory_type,
                content=candidate.content,
                confidence=candidate.confidence,
                state="candidate",
                source_id=source.id,
            )
            session.add(memory)
            memory_rows.append(memory)
        session.flush()

        relationships_created = 0
        for rel in proposal.relationships:
            if 0 <= rel.subject_index < len(memory_rows) and 0 <= rel.object_index < len(
                memory_rows
            ):
                session.add(
                    Relationship(
                        workspace_id=workspace_id,
                        subject_memory_id=memory_rows[rel.subject_index].id,
                        predicate=rel.predicate,
                        object_memory_id=memory_rows[rel.object_index].id,
                        source_id=source.id,
                    )
                )
                relationships_created += 1

        run = IngestRun(
            workspace_id=workspace_id,
            source_id=source.id,
            status="completed",
            completed_at=utcnow(),
            summary=proposal.summary or None,
            metadata_json=json.dumps({"files_written": files_written, "model": proposal.model}),
        )
        session.add(run)

        index_notes(session, workspace_id, config.root_path)
        session.commit()

        return IngestResult(
            source_id=source.id,
            ingest_run_id=run.id,
            files_written=files_written,
            memories_created=len(memory_rows),
            relationships_created=relationships_created,
        )


def strip_srt(raw: str) -> str:
    """Reduce an SRT subtitle file to its spoken text."""
    lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or _SRT_INDEX_RE.match(stripped) or _SRT_TIMING_RE.match(stripped):
            continue
        lines.append(stripped)
    return "\n".join(lines)


# Bracketed timestamps anywhere; bare timestamps only at line start so times
# mentioned in speech ("let's meet at 3:30") survive.
_BRACKET_TS_RE = re.compile(r"[\[(]\d{1,2}:\d{2}(:\d{2})?([,.]\d{1,3})?[\])]")
_LEADING_TS_RE = re.compile(r"^\s*\d{1,2}:\d{2}(:\d{2})?([,.]\d{1,3})?\s*[-–>]*\s*")


def clean_transcript(raw: str) -> str:
    """Light, deterministic transcript cleanup.

    Removes timestamp noise and normalizes whitespace without touching the
    spoken content — the raw capture must stay faithful to the source, so no
    model rewriting happens here.
    """
    cleaned_lines: list[str] = []
    for line in raw.splitlines():
        line = _BRACKET_TS_RE.sub("", line)
        line = _LEADING_TS_RE.sub("", line)
        line = re.sub(r"[ \t]+", " ", line).rstrip()
        if line or (cleaned_lines and cleaned_lines[-1]):
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def slugify(value: str, max_length: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:max_length].rstrip("-") or "untitled"


def load_workspace_guides(config: WorkspaceConfig) -> dict[str, str]:
    """SCHEMA.md (page shape) and RESOLVER.md (routing) excerpts, when present."""
    guides = {}
    for name in ("SCHEMA.md", "RESOLVER.md"):
        path = config.root_path / name
        if path.is_file():
            try:
                guides[name] = path.read_text(encoding="utf-8", errors="replace")[:GUIDE_MAX_CHARS]
            except OSError:
                continue
    return guides


def _enrich_with_model(
    config: WorkspaceConfig, proposal: IngestProposal, client: ModelClient
) -> None:
    related = [(hit.ref, hit.title) for hit in proposal.related_notes]
    prompt = build_ingest_prompt(
        proposal.source_type,
        proposal.origin,
        proposal.text[:MAX_SOURCE_CHARS],
        related,
        context=proposal.context,
        guides=load_workspace_guides(config),
    )
    data = parse_ingest_response(client.complete(INGEST_SYSTEM_PROMPT, prompt))

    proposal.model = client.model
    if isinstance(data.get("title"), str) and data["title"].strip():
        proposal.title = data["title"].strip()
    proposal.summary = str(data.get("summary") or "")
    proposal.key_points = [str(p) for p in data.get("key_points", [])]
    proposal.memories = [
        CandidateMemory(
            memory_type=str(m.get("type") or "fact"),
            content=str(m["content"]),
            confidence=_clamp01(m.get("confidence")),
        )
        for m in data["memories"]
    ]
    proposal.relationships = [
        CandidateRelationship(
            subject_index=int(r["subject"]),
            predicate=str(r["predicate"]),
            object_index=int(r["object"]),
        )
        for r in data["relationships"]
        if isinstance(r.get("subject"), int) and isinstance(r.get("object"), int)
    ]
    if data["proposed_note"] is not None:
        proposal.proposed_note = ProposedFile(
            path=str(data["proposed_note"]["path"]),
            content=str(data["proposed_note"]["markdown"]),
        )


def _build_raw_file(config: WorkspaceConfig, proposal: IngestProposal) -> ProposedFile:
    date = datetime.now(UTC).date().isoformat()
    directory = Path(config.ingest_directory) / RAW_DIRS.get(proposal.source_type, "clippings")
    base = f"{date}-{slugify(proposal.title)}"
    path = _unused_path(config.root_path, directory, base)

    metadata: dict = {
        "type": "source",
        "source_type": proposal.source_type,
        "origin": proposal.origin,
        "title": proposal.title,
        "retrieved": date,
        "status": "raw",
    }
    if proposal.context:
        metadata["context"] = proposal.context
    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)
    return ProposedFile(path=str(path), content=f"---\n{frontmatter}---\n\n" + proposal.text + "\n")


def _sanitize_note(config: WorkspaceConfig, proposal: IngestProposal) -> ProposedFile | None:
    """Keep model-proposed note paths inside the workspace and collision-free."""
    note = proposal.proposed_note
    assert note is not None
    root = config.root_path.resolve()
    candidate = Path(note.path)

    valid = (
        not candidate.is_absolute()
        and candidate.suffix == ".md"
        and (root / candidate).resolve().is_relative_to(root)
    )
    if not valid or (root / candidate).exists():
        # Routing unclear or collision: propose into the drafts directory instead.
        directory = Path(config.generated_directory)
        candidate = _unused_path(root, directory, slugify(proposal.title))
    return ProposedFile(path=str(candidate), content=note.content)


def _unused_path(root: Path, directory: Path, base: str) -> Path:
    path = directory / f"{base}.md"
    counter = 1
    while (root / path).exists():
        path = directory / f"{base}-{counter}.md"
        counter += 1
    return path


def _clamp01(value) -> float | None:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None
