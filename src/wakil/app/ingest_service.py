"""Ingest raw sources into the knowledge base — in two separate steps.

Step 1, capture (`wakil ingest ...`): deterministic only, no model. The
source text is extracted (transcripts get light cleanup), deduped by content
hash, and written under sources/ as a raw capture with frontmatter shaped by
the KB's SCHEMA.md when it defines a template for transcripts/sources —
otherwise transcripts get exactly two fields: create date and meeting date.

Step 2, enrichment (`wakil enrich <source-id>`): the captured source is
analyzed by the model — summary, candidate memories and relationships, and a
proposed KB note linking entities and history — and applied after review.

Each step is itself two-phase (prepare → preview/confirm → apply): prepare
touches nothing, files are only ever created (never overwritten), and DB rows
are only written in apply, so declining a preview leaves no trace.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import frontmatter as frontmatter_lib
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
class CaptureProposal:
    source_type: str
    origin: str
    title: str
    text: str
    content_hash: str
    raw_file: ProposedFile
    context: str | None = None  # user-supplied context (attendees, company, ...)
    meeting_date: str | None = None
    duplicate_of: int | None = None


@dataclass
class CaptureResult:
    source_id: int
    ingest_run_id: int
    raw_file_path: str


@dataclass
class EnrichmentProposal:
    source_id: int
    title: str
    context: str | None = None
    summary: str = ""
    key_points: list[str] = field(default_factory=list)
    memories: list[CandidateMemory] = field(default_factory=list)
    relationships: list[CandidateRelationship] = field(default_factory=list)
    proposed_note: ProposedFile | None = None
    related_notes: list[SearchHit] = field(default_factory=list)
    model: str | None = None


@dataclass
class EnrichmentResult:
    source_id: int
    ingest_run_id: int
    files_written: list[str]
    memories_created: int
    relationships_created: int


# --------------------------------------------------------------------------
# Step 1: capture


def prepare_capture(
    config: WorkspaceConfig,
    kind: str,
    *,
    file: Path | None = None,
    url: str | None = None,
    context: str | None = None,
) -> CaptureProposal:
    meeting_date: str | None = None
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
            meeting_date = infer_meeting_date(file, text)
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
    proposal = CaptureProposal(
        source_type=kind,
        origin=origin,
        title=title,
        text=text,
        content_hash=content_hash,
        context=context,
        meeting_date=meeting_date,
        raw_file=ProposedFile(path="", content=""),
    )

    with open_session(config) as session:
        existing = session.scalar(select(Source.id).where(Source.content_hash == content_hash))
        if existing is not None:
            proposal.duplicate_of = existing
            return proposal

    proposal.raw_file = _build_raw_file(config, proposal)
    return proposal


def apply_capture(config: WorkspaceConfig, proposal: CaptureProposal) -> CaptureResult:
    if proposal.duplicate_of is not None:
        raise IngestError(f"Source already ingested (source id {proposal.duplicate_of})")

    target = config.root_path / proposal.raw_file.path
    if target.exists():
        raise IngestError(f"Refusing to overwrite existing file: {proposal.raw_file.path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(proposal.raw_file.content, encoding="utf-8")

    with open_session(config) as session:
        workspace_id, user_id = _require_workspace_ids(session, config)
        source = Source(
            workspace_id=workspace_id,
            source_type=proposal.source_type,
            title=proposal.title,
            origin=proposal.origin,
            retrieved_at=utcnow(),
            content_hash=proposal.content_hash,
            raw_text_path=proposal.raw_file.path,
            status="raw",
            metadata_json=json.dumps(
                {
                    key: value
                    for key, value in (
                        ("context", proposal.context),
                        ("meeting_date", proposal.meeting_date),
                    )
                    if value
                }
            ),
        )
        session.add(source)
        session.flush()
        run = IngestRun(
            workspace_id=workspace_id,
            source_id=source.id,
            status="completed",
            completed_at=utcnow(),
            summary=f"captured {proposal.source_type}: {proposal.title}",
            metadata_json=json.dumps(
                {"operation": "capture", "files_written": [proposal.raw_file.path]}
            ),
        )
        session.add(run)
        index_notes(session, workspace_id, config.root_path)
        session.commit()
        return CaptureResult(
            source_id=source.id, ingest_run_id=run.id, raw_file_path=proposal.raw_file.path
        )


# --------------------------------------------------------------------------
# Step 2: enrichment


def prepare_enrichment(
    config: WorkspaceConfig,
    source_id: int,
    client: ModelClient,
    context: str | None = None,
    force: bool = False,
) -> EnrichmentProposal:
    with open_session(config) as session:
        source = session.get(Source, source_id)
        if source is None:
            raise IngestError(f"No source with id {source_id}. See capture output for ids.")
        if source.status == "enriched" and not force:
            raise IngestError(
                f"Source {source_id} is already enriched; pass --force to re-analyze."
            )
        metadata = json.loads(source.metadata_json or "{}")
        context = context or metadata.get("context")
        text = _load_source_text(config, source)
        title = source.title or f"source {source_id}"

        related_query = " ".join(filter(None, [title, context, text[:300]]))
        related_notes = [
            hit
            for hit in search_workspace(
                config=config, session=session, query=related_query, limit=RELATED_NOTE_LIMIT
            )
            if hit.kind == "note" and hit.ref != source.raw_text_path
        ]

    proposal = EnrichmentProposal(
        source_id=source_id, title=title, context=context, related_notes=related_notes
    )

    prompt = build_ingest_prompt(
        source.source_type,
        source.origin or title,
        text[:MAX_SOURCE_CHARS],
        [(hit.ref, hit.title) for hit in related_notes],
        context=context,
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
        proposal.proposed_note = _sanitize_note(
            config,
            ProposedFile(
                path=str(data["proposed_note"]["path"]),
                content=str(data["proposed_note"]["markdown"]),
            ),
            proposal.title,
        )
    return proposal


def apply_enrichment(config: WorkspaceConfig, proposal: EnrichmentProposal) -> EnrichmentResult:
    files_written: list[str] = []
    if proposal.proposed_note is not None:
        target = config.root_path / proposal.proposed_note.path
        if target.exists():
            raise IngestError(f"Refusing to overwrite existing file: {proposal.proposed_note.path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(proposal.proposed_note.content, encoding="utf-8")
        files_written.append(proposal.proposed_note.path)

    with open_session(config) as session:
        workspace_id, user_id = _require_workspace_ids(session, config)
        source = session.get(Source, proposal.source_id)
        if source is None:
            raise IngestError(f"No source with id {proposal.source_id}.")

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

        source.status = "enriched"
        source.title = proposal.title
        metadata = json.loads(source.metadata_json or "{}")
        if proposal.summary:
            metadata["summary"] = proposal.summary
        if proposal.context:
            metadata["context"] = proposal.context
        source.metadata_json = json.dumps(metadata)

        run = IngestRun(
            workspace_id=workspace_id,
            source_id=source.id,
            status="completed",
            completed_at=utcnow(),
            summary=proposal.summary or None,
            metadata_json=json.dumps(
                {
                    "operation": "enrich",
                    "files_written": files_written,
                    "model": proposal.model,
                }
            ),
        )
        session.add(run)
        index_notes(session, workspace_id, config.root_path)
        session.commit()

        return EnrichmentResult(
            source_id=source.id,
            ingest_run_id=run.id,
            files_written=files_written,
            memories_created=len(memory_rows),
            relationships_created=relationships_created,
        )


# --------------------------------------------------------------------------
# Text extraction and cleanup


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


_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_COMPACT_DATE_RE = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)")


def infer_meeting_date(file: Path, text: str) -> str | None:
    """Best-effort meeting date: filename first, then the transcript opening."""
    for pattern in (_ISO_DATE_RE, _COMPACT_DATE_RE):
        match = pattern.search(file.name)
        if match:
            return "-".join(match.groups())
    head = "\n".join(text.splitlines()[:5])
    match = _ISO_DATE_RE.search(head)
    return match.group(0) if match else None


def slugify(value: str, max_length: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:max_length].rstrip("-") or "untitled"


# --------------------------------------------------------------------------
# Frontmatter and workspace guidance


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


_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_YAML_BLOCK_RE = re.compile(r"```ya?ml\s*\n(.*?)```", re.DOTALL)


def transcript_frontmatter_template(config: WorkspaceConfig) -> dict | None:
    """A frontmatter template for transcripts from SCHEMA.md, if it defines one.

    Looks for a fenced yaml block inside a SCHEMA.md section whose heading
    mentions transcripts (or, failing that, sources). Returns None when
    SCHEMA.md is absent or defines nothing usable — the caller then falls
    back to the minimal two-field frontmatter.
    """
    schema_path = config.root_path / "SCHEMA.md"
    if not schema_path.is_file():
        return None
    try:
        text = schema_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    headings = list(_HEADING_RE.finditer(text))
    for keyword in ("transcript", "source"):
        for i, heading in enumerate(headings):
            if keyword not in heading.group(1).lower():
                continue
            section_end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
            block = _YAML_BLOCK_RE.search(text, heading.end(), section_end)
            if block:
                try:
                    template = yaml.safe_load(block.group(1))
                except yaml.YAMLError:
                    continue
                if isinstance(template, dict) and template:
                    return template
    return None


# Template keys we know how to fill, in normalized form.
_KNOWN_FIELD_VALUES = {
    "created": "created",
    "create_date": "created",
    "date_created": "created",
    "meeting_date": "meeting_date",
    "date": "meeting_date",
    "title": "title",
    "name": "title",
    "origin": "origin",
    "source_file": "origin",
    "context": "context",
}


def _build_raw_file(config: WorkspaceConfig, proposal: CaptureProposal) -> ProposedFile:
    created = datetime.now(UTC).date().isoformat()
    directory = Path(config.ingest_directory) / RAW_DIRS.get(proposal.source_type, "clippings")
    slug = slugify(proposal.title)
    # Avoid a doubled date when the source filename already carried one.
    slug = re.sub(r"^\d{4}-?\d{2}-?\d{2}-?", "", slug) or "untitled"
    base = f"{proposal.meeting_date or created}-{slug}"
    path = _unused_path(config.root_path, directory, base)

    if proposal.source_type == "transcript":
        metadata = _transcript_metadata(config, proposal, created)
    else:
        metadata = {
            "type": "source",
            "source_type": proposal.source_type,
            "origin": proposal.origin,
            "title": proposal.title,
            "retrieved": created,
            "status": "raw",
        }
        if proposal.context:
            metadata["context"] = proposal.context

    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)
    return ProposedFile(path=str(path), content=f"---\n{frontmatter}---\n\n" + proposal.text + "\n")


def _transcript_metadata(config: WorkspaceConfig, proposal: CaptureProposal, created: str) -> dict:
    values = {
        "created": created,
        "meeting_date": proposal.meeting_date,
        "title": proposal.title,
        "origin": proposal.origin,
        "context": proposal.context,
    }
    template = transcript_frontmatter_template(config)
    if template is None:
        # SCHEMA.md defines nothing for transcripts: exactly two fields.
        return {"created": created, "meeting_date": proposal.meeting_date}
    metadata = {}
    for key, template_value in template.items():
        normalized = _KNOWN_FIELD_VALUES.get(key.lower().replace("-", "_"))
        filled = values.get(normalized) if normalized else None
        metadata[key] = filled if filled is not None else template_value
    return metadata


def _load_source_text(config: WorkspaceConfig, source: Source) -> str:
    if not source.raw_text_path:
        raise IngestError(f"Source {source.id} has no raw capture on disk.")
    path = config.root_path / source.raw_text_path
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise IngestError(f"Could not read raw capture {source.raw_text_path}: {exc}") from exc
    try:
        return frontmatter_lib.loads(raw).content
    except Exception:
        return raw


def _sanitize_note(config: WorkspaceConfig, note: ProposedFile, title: str) -> ProposedFile:
    """Keep model-proposed note paths inside the workspace and collision-free."""
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
        candidate = _unused_path(root, directory, slugify(title))
    return ProposedFile(path=str(candidate), content=note.content)


def _unused_path(root: Path, directory: Path, base: str) -> Path:
    path = directory / f"{base}.md"
    counter = 1
    while (root / path).exists():
        path = directory / f"{base}-{counter}.md"
        counter += 1
    return path


def _require_workspace_ids(session, config: WorkspaceConfig) -> tuple[int, int]:
    workspace_id = session.scalar(
        select(Workspace.id).where(Workspace.root_path == str(config.root_path))
    )
    user_id = session.scalar(select(User.id))
    if workspace_id is None or user_id is None:
        raise IngestError("Workspace database is not initialized; run `wakil init` first.")
    return workspace_id, user_id


def _clamp01(value) -> float | None:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None
