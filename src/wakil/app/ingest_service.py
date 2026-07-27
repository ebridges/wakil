"""Ingest raw sources into the knowledge base — in two separate steps.

Step 1, capture (`wakil ingest ...`): deterministic except for one small
model call (docs/adr/0010-capture-time-title-and-abstract-generation.md).
The source text is extracted (transcripts get light cleanup), deduped by
content hash, and written under sources/ as a raw capture with frontmatter
shaped by the `source` entity schema's base fields plus its `transcript`
origin sub-schema (`schema/entities/source.yaml`) — the schema catalog, not
a workspace-authored template, is the source of truth for this shape. The
raw file's path/slug stays fully deterministic (derived from the
filename/article scrape, never the model); only the frontmatter
`title:`/`abstract:` content comes from the model, via a single cheap call
against the `CaptureMetadata` contract.

Step 2, enrichment (`wakil enrich <source-id>`): a fixed, code-sequenced DAG
(docs/ingestion-refactor-spec.md) — an extraction model call (judgment from
the `<kind>` skill against the ExtractionOutput contract), then an
always-invoked entity-resolution model call (the `entity-resolve` skill
against EntityResolution), then `validate_proposal()` gating every proposed
new file against the entity schemas, then one preview/confirm, then apply.
Both skills are resolved through `wakil.skills.resolver` (built-in by
default, kb-local/user overridable) via `wakil.llm.skill_loader.load_skill`.

Each step is itself two-phase (prepare → preview/confirm → apply): prepare
touches nothing, files are only ever created (never overwritten), and DB rows
are only written in apply, so declining a preview leaves no trace.
"""

import contextlib
import hashlib
import json
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import frontmatter as frontmatter_lib
import yaml
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from wakil.app.search_service import SearchHit, search_workspace
from wakil.app.workspace_service import index_notes, open_session
from wakil.config.settings import WorkspaceConfig
from wakil.integrations.web import fetch_article
from wakil.knowledge.wikilinks import WIKILINK_RE as _WIKILINK_RE
from wakil.knowledge.wikilinks import normalize_target as _normalize_link_path
from wakil.llm.client import ModelClient
from wakil.llm.prompts import (
    CAPTURE_METADATA_SYSTEM_PROMPT,
    build_capture_metadata_prompt,
    build_compile_prompt,
    build_extraction_prompt,
    build_full_resynthesis_prompt,
    build_resolution_prompt,
    build_revision_prompt,
)
from wakil.llm.schemas import (
    CaptureMetadata,
    EntityCompileOutput,
    EntityResolution,
    EntityResolutionOutput,
    EntityRevision,
    EntityRevisionOutput,
    ExtractionOutput,
    ModelContractError,
    complete_with_contract,
)
from wakil.llm.skill_loader import SkillLoadError, build_system_prompt, load_skill
from wakil.schema.loader import load_entity_schemas, resolve_page_shape_template
from wakil.schema.validate import validate_frontmatter
from wakil.storage.schema import (
    IngestRun,
    Memory,
    Note,
    Relationship,
    Source,
    User,
    Workspace,
    utcnow,
)

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
    # Commitment/register axis, orthogonal to memory_type (docs/adr/0014).
    stance: str | None = None
    # The dated event's own date (memory_type="event"), for Timeline ordering.
    event_date: date | None = None


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
    # `context` with any `--- Attached Context ---` blocks excluded, and the
    # KB-relative paths of the @file: references that produced them — see
    # context_references.resolve_context. Persisted alongside `context` so a
    # later `wakil enrich` without repeating --context still benefits.
    context_digest: str | None = None
    context_referenced_paths: list[str] = field(default_factory=list)
    meeting_date: str | None = None
    duplicate_of: int | None = None
    abstract: str | None = None  # model-generated, docs/adr/0010


@dataclass
class CaptureResult:
    source_id: int
    ingest_run_id: int
    raw_file_path: str


@dataclass
class AbstractBackfillItem:
    """One source captured before docs/adr/0010, plus the title/abstract a
    fresh capture-metadata call generated for it."""

    source_id: int
    raw_text_path: str
    old_title: str | None
    title: str
    abstract: str


@dataclass
class EntityUpdate:
    """A proposed, deterministic edit to an *existing* note — DAG node 3's
    output. `old_content` is what was read during prepare; apply_enrichment
    re-reads and refuses to apply if the file changed since (mirrors
    schema_migrate_service's stale-file guard)."""

    target_note_path: str
    old_content: str
    new_content: str


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
    # What the entity-resolution step decided, and the stub pages it implies.
    entity_resolutions: list[EntityResolution] = field(default_factory=list)
    stub_entities: list[ProposedFile] = field(default_factory=list)
    # Deterministic merges into existing notes for action=update resolutions
    # entity-resolution decided actually warrant a content change.
    entity_updates: list[EntityUpdate] = field(default_factory=list)
    # Visible degradations (a failed resolution call, a downgraded create) —
    # shown in the preview, never silently swallowed.
    warnings: list[str] = field(default_factory=list)
    model: str | None = None


@dataclass
class ProposalIssue:
    """A validation failure that blocks apply (hard stop, not best-guess)."""

    location: str  # proposed file path or "entity:<name>"
    message: str

    def __str__(self) -> str:
        return f"{self.location}: {self.message}"


@dataclass
class EnrichmentResult:
    source_id: int
    ingest_run_id: int
    files_written: list[str]
    memories_created: int
    relationships_created: int
    # Entity updates skipped because the target changed on disk since this
    # enrichment was prepared — the preview's warnings are shown pre-apply
    # and don't cover this, so it's surfaced here instead.
    stale_updates_skipped: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Step 1: capture


def prepare_capture(
    config: WorkspaceConfig,
    kind: str,
    client: ModelClient,
    *,
    file: Path | None = None,
    url: str | None = None,
    context: str | None = None,
    context_digest: str | None = None,
    context_referenced_paths: list[str] | None = None,
) -> CaptureProposal:
    meeting_date: str | None = None
    if kind in ("transcript", "text"):
        if file is None:
            raise IngestError(f"{kind} ingest needs a file path")
        if kind == "transcript" and file.suffix.lower() == ".whisper":
            text, recorded_at = parse_whisper_transcript(file)
            meeting_date = infer_meeting_date(file, text) or recorded_at
        elif kind == "transcript" and file.suffix.lower() == ".json":
            text = parse_json_transcript(file)
            meeting_date = infer_meeting_date(file, text)
        else:
            try:
                raw = file.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise IngestError(f"Could not read {file}: {exc}") from exc
            text = strip_srt(raw) if file.suffix.lower() == ".srt" else raw
            if kind == "transcript":
                text = clean_transcript(text)
                meeting_date = infer_meeting_date(file, text)
        origin = _relative_origin(config, file)
        stem = _LEADING_DATE_RE.sub("", file.stem) or file.stem
        # Deterministic basis for the raw file's slug only -- see below,
        # this is intentionally never overwritten by the model's title.
        slug_source = stem.replace("-", " ").replace("_", " ").strip() or file.name
    elif kind == "article":
        if url is None:
            raise IngestError("article ingest needs a URL")
        article = fetch_article(url)
        text = article.text
        origin = url
        slug_source = article.title
    else:
        raise IngestError(f"unknown ingest kind: {kind}")

    if not text.strip():
        raise IngestError("Source contains no text")

    content_hash = hashlib.sha256(text.encode()).hexdigest()
    proposal = CaptureProposal(
        source_type=kind,
        origin=origin,
        title=slug_source,
        text=text,
        content_hash=content_hash,
        context=context,
        context_digest=context_digest,
        context_referenced_paths=list(context_referenced_paths or []),
        meeting_date=meeting_date,
        raw_file=ProposedFile(path="", content=""),
    )

    with open_session(config) as session:
        existing = session.scalar(select(Source.id).where(Source.content_hash == content_hash))
        if existing is not None:
            proposal.duplicate_of = existing
            return proposal

    metadata = _generate_capture_metadata(client, kind, origin, text, context)
    proposal.title = metadata.title
    proposal.abstract = metadata.abstract
    proposal.raw_file = _build_raw_file(config, proposal, slug_source)
    return proposal


def _generate_capture_metadata(
    client: ModelClient, source_type: str, origin: str, text: str, context: str | None
) -> CaptureMetadata:
    """The one model call capture makes (docs/adr/0010): title + abstract,
    grounded in the captured text itself rather than just the filename."""
    today = datetime.now(UTC).date().isoformat()
    prompt = build_capture_metadata_prompt(
        source_type, origin, text[:MAX_SOURCE_CHARS], today, context=context
    )
    try:
        return complete_with_contract(
            client, CAPTURE_METADATA_SYSTEM_PROMPT, prompt, CaptureMetadata
        )
    except ModelContractError as exc:
        raise IngestError(f"Capture metadata generation failed: {exc}") from exc


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
                        ("context_digest", proposal.context_digest),
                        ("context_referenced_paths", proposal.context_referenced_paths),
                        ("title", proposal.title),
                        ("abstract", proposal.abstract),
                    )
                    if value
                }
            ),
        )
        session.add(source)
        try:
            session.flush()
        except IntegrityError:
            # Lost a race: another process captured identical content (same
            # workspace_id, content_hash) between prepare_capture's check
            # and this insert -- the uq_sources_workspace_content_hash
            # constraint is what actually closes that window; this is just
            # surfacing it the same way an early duplicate-of hit is.
            session.rollback()
            target.unlink(missing_ok=True)
            existing_id = session.scalar(
                select(Source.id).where(
                    Source.workspace_id == workspace_id,
                    Source.content_hash == proposal.content_hash,
                )
            )
            raise IngestError(
                f"Source already ingested (source id {existing_id}); "
                "lost a race with a concurrent identical capture"
            ) from None
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
        index_notes(session, workspace_id, config.root_path, prune=not config.is_linked_worktree)
        session.commit()
        return CaptureResult(
            source_id=source.id, ingest_run_id=run.id, raw_file_path=proposal.raw_file.path
        )


# --------------------------------------------------------------------------
# Backfill: title/abstract for sources captured before docs/adr/0010.
# Metadata-only -- never re-runs extraction/entity-resolution, never touches
# memories or relationships. Mirrors capture/enrichment's own prepare/apply
# split: planning calls the model and touches nothing, apply is the only
# step that writes.


def plan_abstract_backfill(
    config: WorkspaceConfig, client: ModelClient
) -> list[AbstractBackfillItem]:
    """Sources whose metadata_json has no `abstract` key yet -- one capture-
    metadata model call per source, same contract as capture itself."""
    items: list[AbstractBackfillItem] = []
    with open_session(config) as session:
        sources = session.scalars(select(Source).order_by(Source.id)).all()
        for source in sources:
            metadata = json.loads(source.metadata_json or "{}")
            if metadata.get("abstract") or not source.raw_text_path:
                continue
            try:
                text = _load_source_text(config, source)
            except IngestError:
                continue
            generated = _generate_capture_metadata(
                client, source.source_type, source.origin or "", text, metadata.get("context")
            )
            items.append(
                AbstractBackfillItem(
                    source_id=source.id,
                    raw_text_path=source.raw_text_path,
                    old_title=source.title,
                    title=generated.title,
                    abstract=generated.abstract,
                )
            )
    return items


def apply_abstract_backfill(
    config: WorkspaceConfig, items: list[AbstractBackfillItem]
) -> list[str]:
    """Rewrite each item's raw file frontmatter (title/abstract keys only --
    a python-frontmatter round-trip preserves everything else, including
    field order, byte-for-byte) and the matching Source row."""
    updated: list[str] = []
    with open_session(config) as session:
        for item in items:
            source = session.get(Source, item.source_id)
            if source is None:
                continue
            target = config.root_path / item.raw_text_path
            try:
                raw = target.read_text(encoding="utf-8")
            except OSError:
                continue
            post = frontmatter_lib.loads(raw)
            post["title"] = item.title
            post["abstract"] = item.abstract
            target.write_text(
                frontmatter_lib.dumps(post, sort_keys=False) + "\n", encoding="utf-8"
            )

            metadata = json.loads(source.metadata_json or "{}")
            metadata["title"] = item.title
            metadata["abstract"] = item.abstract
            source.title = item.title
            source.metadata_json = json.dumps(metadata)
            updated.append(item.raw_text_path)
        session.commit()
    return updated


# --------------------------------------------------------------------------
# Source audit trail: list/show already-captured sources (`wakil sources
# list|show`). Read-only -- the status vocabulary a source actually moves
# through is just "raw" (apply_capture, the only place a Source row gets
# created) -> "enriched" (apply_enrichment); the "new" column default is
# never written by current code, it only guards rows some future writer
# might add without going through capture.


@dataclass
class SourceSummary:
    """One `wakil sources list`/`show` row -- a flattened, detached snapshot
    of a Source row, not the live ORM object (the session that read it is
    closed by the time the CLI prints it)."""

    id: int
    source_type: str
    title: str | None
    origin: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    git_branch: str | None
    git_pr_url: str | None
    raw_text_path: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    content_hash: str | None = None


def _summarize_source(row: Source) -> SourceSummary:
    return SourceSummary(
        id=row.id,
        source_type=row.source_type,
        title=row.title,
        origin=row.origin,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
        git_branch=row.git_branch,
        git_pr_url=row.git_pr_url,
        raw_text_path=row.raw_text_path,
        author=row.author,
        published_at=row.published_at,
        retrieved_at=row.retrieved_at,
        content_hash=row.content_hash,
    )


def list_sources(
    config: WorkspaceConfig, status: str | None = None, limit: int | None = 50
) -> list[SourceSummary]:
    """Sources for this workspace, most recent first. `limit=None` returns
    every row -- used for the post-batch audit pass before opening a
    migration category's PR, where "did anything get missed" matters more
    than a short list."""
    with open_session(config) as session:
        workspace_id, _ = _require_workspace_ids(session, config)
        stmt = (
            select(Source)
            .where(Source.workspace_id == workspace_id)
            .order_by(Source.created_at.desc(), Source.id.desc())
        )
        if status is not None:
            stmt = stmt.where(Source.status == status)
        if limit is not None:
            stmt = stmt.limit(limit)
        return [_summarize_source(row) for row in session.scalars(stmt)]


def get_source(config: WorkspaceConfig, source_id: int) -> SourceSummary:
    with open_session(config) as session:
        workspace_id, _ = _require_workspace_ids(session, config)
        row = session.get(Source, source_id)
        if row is None or row.workspace_id != workspace_id:
            raise IngestError(f"No source with id {source_id} in this workspace.")
        return _summarize_source(row)


# --------------------------------------------------------------------------
# Step 2: enrichment


def prepare_enrichment(
    config: WorkspaceConfig,
    source_id: int,
    client: ModelClient,
    context: str | None = None,
    context_digest: str | None = None,
    context_referenced_paths: list[str] | None = None,
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
        context_digest = context_digest or metadata.get("context_digest")
        context_referenced_paths = (
            context_referenced_paths or metadata.get("context_referenced_paths") or []
        )
        # Sources captured before context_digest existed have none stored —
        # fall back to the raw context so query-building still works.
        search_context = context_digest or context
        text = _load_source_text(config, source)
        title = source.title or f"source {source_id}"
        workspace_id, _ = _require_workspace_ids(session, config)

        # @file:-referenced notes are guaranteed related notes, ahead of and
        # not subject to RELATED_NOTE_LIMIT: the user pointed at them
        # explicitly, so relevance ranking shouldn't get a vote.
        related_notes: list[SearchHit] = []
        seen_paths: set[str] = set()
        for path in context_referenced_paths:
            if path == source.raw_text_path or path in seen_paths:
                continue
            seen_paths.add(path)
            note = session.scalar(
                select(Note).where(Note.workspace_id == workspace_id, Note.path == path)
            )
            related_notes.append(
                SearchHit(
                    kind="note",
                    ref=path,
                    title=note.title if note and note.title else _deslug(path),
                    snippet="",
                    engine="user-referenced",
                )
            )

        related_query = " ".join(filter(None, [title, search_context, text[:300]]))
        for hit in search_workspace(
            config=config, session=session, query=related_query, limit=RELATED_NOTE_LIMIT
        ):
            if hit.kind != "note" or hit.ref == source.raw_text_path or hit.ref in seen_paths:
                continue
            seen_paths.add(hit.ref)
            related_notes.append(hit)

        # Relevance search optimizes for "notes that talk about X" and reliably
        # buries a short entity stub (title literally "Mosaic") behind longer
        # notes that just mention the name often. Entity resolution needs the
        # opposite question answered — "does a page named X already exist" —
        # so supplement with a direct title lookup against known entity
        # directories, independent of QMD/FTS ranking.
        for path, note_title in _candidate_entity_notes(
            session,
            workspace_id,
            " ".join(filter(None, [search_context, text])),
            load_entity_schemas(config.root_path),
            extra_terms=_title_terms(title),
        ):
            if path in seen_paths or path == source.raw_text_path:
                continue
            seen_paths.add(path)
            related_notes.append(
                SearchHit(kind="note", ref=path, title=note_title, snippet="", engine="entity-name")
            )

    proposal = EnrichmentProposal(
        source_id=source_id, title=title, context=context, related_notes=related_notes
    )
    proposal.model = client.model
    guides = load_workspace_guides(config)
    related_pairs = [(hit.ref, hit.title) for hit in related_notes]
    source_text = text[:MAX_SOURCE_CHARS]

    # DAG node 1: extraction judgment (the <kind> skill + ExtractionOutput).
    # The raw *capture* path (sources/transcripts/...), not source.origin's
    # pre-capture location — origin may be a binary/external file (a
    # .whisper archive, a URL) the model can't cite as a KB source.
    extraction = _run_extraction(
        config,
        client,
        source.source_type,
        source.raw_text_path or source.origin or title,
        source_text,
        related_pairs,
        proposal,
        guides,
    )
    if extraction.title and extraction.title.strip():
        proposal.title = extraction.title.strip()
    proposal.summary = extraction.summary
    proposal.key_points = list(extraction.key_points)
    proposal.memories = [
        CandidateMemory(
            memory_type=m.type or "fact",
            content=m.content,
            confidence=_clamp01(m.confidence),
            stance=m.stance,
            event_date=m.event_date,
        )
        for m in extraction.memories
        if m.content.strip()
    ]
    proposal.relationships = [
        CandidateRelationship(subject_index=r.subject, predicate=r.predicate, object_index=r.object)
        for r in extraction.relationships
    ]
    if extraction.proposed_note is not None:
        proposal.proposed_note = _sanitize_note(
            config,
            ProposedFile(
                path=extraction.proposed_note.path,
                content=extraction.proposed_note.markdown,
            ),
            proposal.title,
        )

    # DAG node 2: entity resolution — always invoked, never optional.
    _run_entity_resolution(config, client, source_text, related_pairs, proposal, guides)
    return proposal


# A run of 1-4 capitalized words: "Mosaic", "Ian Gutwinski", "Riviera Partners".
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,3}\b")

# The ~5000 most frequent English words (data/common_words.zip, ranked by
# frequency in conversational/subtitle text — see data/common_words.SOURCE.md
# for provenance/license), used below to drop single-token candidates that
# are ordinary function words, backchannel markers ("well", "so", "right",
# "okay", "mm-hmm"), or nouns/adjectives swept up during tangential small
# talk ("Indian", "Steak") rather than genuine proper nouns. Only ever
# applied to single-token candidates: a 2-4 word run like "Ian Gutwinski" or
# "Riviera Partners" is a much stronger name signal, and short legitimate
# company/person names ("Mosaic") must still survive. Zipped rather than
# stored as plain text to keep this vendored file small in the repo;
# decompressed once at import time, not re-read per call.
with zipfile.ZipFile(Path(__file__).parent / "data" / "common_words.zip") as _archive:
    _COMMON_SINGLE_WORDS = frozenset(_archive.read("common_words.txt").decode().split())

# Exact phrases that regex-match as capitalized word runs but are structural
# artifacts, not names — the context-expansion delimiter (see
# wakil.app.context_references) is the only known case.
_PHRASE_STOPWORDS = {"attached context"}


def _is_noise_candidate(phrase: str) -> bool:
    lowered = phrase.lower()
    if lowered in _PHRASE_STOPWORDS:
        return True
    if " " in phrase:
        return False
    return lowered in _COMMON_SINGLE_WORDS


# Generic words in a humanized filename/title ("offer", "sync", "call") that
# would otherwise substring-match unrelated entity notes once casing is no
# longer a filter (see _title_terms). A separate, smaller mechanism from
# _is_noise_candidate above: filename-derived terms carry no capitalization
# signal, so the frequency-based common-word filter (tuned for prose, where
# "kyle" or "carnes" are common enough to be noise) is too aggressive here.
_TITLE_TERM_STOPWORDS = {
    "i",
    "the",
    "a",
    "an",
    "meeting",
    "call",
    "phone",
    "yeah",
    "well",
    "so",
    "but",
    "and",
    "this",
    "that",
    "we",
    "she",
    "he",
    "they",
    "sync",
    "offer",
    "prep",
    "recap",
    "update",
    "follow",
    "notes",
    "chat",
    "intro",
}


def _title_terms(title: str) -> set[str]:
    """Lowercase word tokens from a humanized filename/title.

    A whisper capture's title is derived from the filename by lowercasing
    and de-hyphenating (see prepare_capture) — it carries no capitalization
    signal, so _PROPER_NOUN_RE can't see it. A call's company/person is
    routinely named only in the filename convention (an interview thread's
    audio files, say), never spoken aloud in the transcript body itself —
    without this, an existing entity page for that name is never even
    offered to entity-resolution as a candidate, and it proposes a
    duplicate page instead of recognizing the one that already exists.
    """
    words = re.split(r"[^a-zA-Z0-9]+", title.lower())
    return {w for w in words if len(w) > 2 and w not in _TITLE_TERM_STOPWORDS}


def _candidate_entity_notes(
    session, workspace_id: int, text: str, schemas: dict, *, extra_terms: set[str] = frozenset()
) -> list[tuple[str, str]]:
    """Direct title lookup for proper nouns against known entity directories.

    Complements (does not replace) the relevance search above: a name match
    here doesn't depend on QMD/FTS ranking a short, sparse entity stub above
    longer notes that merely mention the same name.

    extra_terms: additional lowercase candidate words (e.g. from
    _title_terms) that bypass the capitalized-proper-noun requirement —
    for text sources where casing can't be trusted as a name signal.
    """
    directories = tuple(
        f"{schema.directory}/"
        for schema in schemas.values()
        if schema.directory and schema.category in ("identity", "hybrid")
    )
    if not directories:
        return []
    candidates = {
        phrase
        for phrase in _PROPER_NOUN_RE.findall(text)
        if len(phrase) > 2 and not _is_noise_candidate(phrase)
    }
    candidates |= extra_terms
    if not candidates:
        return []
    notes = session.scalars(
        select(Note).where(
            Note.workspace_id == workspace_id,
            or_(*(Note.path.like(f"{d}%") for d in directories)),
        )
    )
    matches: list[tuple[str, str]] = []
    for note in notes:
        note_title = note.title or ""
        if not note_title:
            continue
        if any(
            phrase.lower() in note_title.lower() or note_title.lower() in phrase.lower()
            for phrase in candidates
        ):
            matches.append((note.path, note_title))
    return matches


def _run_extraction(
    config: WorkspaceConfig,
    client: ModelClient,
    source_type: str,
    origin: str,
    text: str,
    related_pairs: list[tuple[str, str]],
    proposal: EnrichmentProposal,
    guides: dict[str, str],
) -> ExtractionOutput:
    try:
        skill = load_skill(source_type, config.root_path)
    except SkillLoadError:
        # unknown kinds get the generic clipping judgment
        skill = load_skill("text", config.root_path)
    system = build_system_prompt(skill, ExtractionOutput)
    schemas = load_entity_schemas(config.root_path)
    page_shapes = {
        schema.page_shape: resolve_page_shape_template(schema.page_shape, config.root_path)[0]
        for schema in schemas.values()
    }
    prompt = build_extraction_prompt(
        source_type,
        origin,
        text,
        related_pairs,
        schemas,
        page_shapes,
        context=proposal.context,
        guides=guides,
    )
    try:
        return complete_with_contract(client, system, prompt, ExtractionOutput)
    except ModelContractError as exc:
        # Nothing downstream can run without extraction: fail visibly here.
        raise IngestError(f"Enrichment extraction failed: {exc}") from exc


def _run_entity_resolution(
    config: WorkspaceConfig,
    client: ModelClient,
    text: str,
    related_pairs: list[tuple[str, str]],
    proposal: EnrichmentProposal,
    guides: dict[str, str],
) -> None:
    """Second model call plus stub-page construction; degrades visibly."""
    skill = load_skill("entity-resolve", config.root_path)
    system = build_system_prompt(skill, EntityResolutionOutput)
    prompt = build_resolution_prompt(
        text,
        proposal.summary,
        proposal.proposed_note.content if proposal.proposed_note else None,
        related_pairs,
        load_entity_schemas(config.root_path),
        context=proposal.context,
        guides=guides,
    )
    try:
        resolution = complete_with_contract(client, system, prompt, EntityResolutionOutput)
    except ModelContractError as exc:
        proposal.warnings.append(f"Entity resolution failed; no entity pages proposed: {exc}")
        return
    proposal.entity_resolutions = list(resolution.entities)
    proposal.stub_entities = _build_stub_entities(config, proposal)
    _reconcile_entity_links(config, proposal)
    _run_entity_updates(config, client, text, proposal)


def _build_stub_entities(
    config: WorkspaceConfig, proposal: EnrichmentProposal
) -> list[ProposedFile]:
    """One stub page per notable new entity (action=create), schema-routed.

    Unknown entity types and types without a canonical directory build no
    stub here — validate_proposal() reports them as hard stops, and a
    warning is recorded here so the skip is visible in the enrich preview
    even when validate_proposal isn't run against this exact resolution.
    """
    schemas = load_entity_schemas(config.root_path)
    today = datetime.now(UTC).date().isoformat()
    stubs: list[ProposedFile] = []
    taken = {proposal.proposed_note.path} if proposal.proposed_note else set()

    for resolution in proposal.entity_resolutions:
        if resolution.action != "create":
            continue
        schema = schemas.get(resolution.entity_type)
        if schema is None or schema.directory is None:
            proposal.warnings.append(
                f"{resolution.name}: type '{resolution.entity_type}' has no canonical "
                "directory to route into — needs manual placement"
            )
            continue
        path = f"{schema.directory}/{slugify(resolution.name)}.md"
        if (config.root_path / path).exists():
            proposal.warnings.append(
                f"{resolution.name}: {path} already exists — not creating a duplicate page"
            )
            continue
        if path in taken:
            continue
        taken.add(path)

        proposed = dict(resolution.proposed_frontmatter or {})
        proposed.pop("type", None)
        label_field = "title" if schema.category == "document" else "name"
        metadata: dict = {
            "type": resolution.entity_type,
            label_field: proposed.pop(label_field, None) or resolution.name,
        }
        metadata.update(proposed)
        for date_field in ("created", "updated"):
            if date_field in schema.fields and not metadata.get(date_field):
                metadata[date_field] = today
        stubs.append(ProposedFile(path=path, content=_stub_content(metadata, resolution.name)))
    return stubs


# Wikilink parsing/normalization live in wakil.knowledge.wikilinks (shared
# with index-time extraction) — imported at the top of the module. This
# section retains only the entity-resolution reconciliation logic that
# builds on that parse.


def _deslug(path: str) -> str:
    """Comparable text for a wikilink with no `|display` part: its slug, deslugged."""
    stem = path.strip().rsplit("/", 1)[-1]
    if stem.endswith(".md"):
        stem = stem[:-3]
    return stem.replace("-", " ").replace("_", " ")


def _reconcile_entity_links(config: WorkspaceConfig, proposal: EnrichmentProposal) -> None:
    """Correct proposed_note wikilinks that disagree with entity resolution.

    Extraction (proposed_note.markdown) and entity resolution
    (entity_resolutions) are independent model calls; both can decide where
    an entity mention should link, and they can disagree — extraction wrote
    the note's prose without seeing entity-resolution's final answer.
    Entity-resolution's own decision is already treated as authoritative
    everywhere else (_build_stub_entities, validate_proposal), so this makes
    the note's prose agree with it too: a conservative, exact-match-only
    string rewrite, never a new model call, and never silent — any
    correction is recorded in proposal.warnings.
    """
    if proposal.proposed_note is None:
        return

    schemas = load_entity_schemas(config.root_path)
    stub_paths = {stub.path for stub in proposal.stub_entities}

    authoritative: dict[str, str] = {}
    for resolution in proposal.entity_resolutions:
        if resolution.action == "update":
            if resolution.target_note_path:
                authoritative[resolution.name.lower()] = resolution.target_note_path
        elif resolution.action == "create":
            schema = schemas.get(resolution.entity_type)
            if schema is None or schema.directory is None:
                continue  # no schema/directory: _build_stub_entities built no stub
            path = f"{schema.directory}/{slugify(resolution.name)}.md"
            if path in stub_paths:
                authoritative[resolution.name.lower()] = path
            # else: stub was skipped (already exists / path collision) — no
            # authoritative path to correct against, leave matching links alone.
        # action == "skip": no correction target.

    if not authoritative:
        return

    corrections: list[str] = []

    def _replace(match: re.Match) -> str:
        path = match.group(1).strip()
        display = match.group(2)
        key = (display.strip() if display else _deslug(path)).lower()
        target = authoritative.get(key)
        if target is None or _normalize_link_path(target) == _normalize_link_path(path):
            return match.group(0)
        old = f"[[{path}|{display}]]" if display is not None else f"[[{path}]]"
        new = f"[[{target}|{display}]]" if display is not None else f"[[{target}]]"
        corrections.append(f"{old} -> {new}")
        return new

    new_content = _WIKILINK_RE.sub(_replace, proposal.proposed_note.content)
    if corrections:
        proposal.proposed_note.content = new_content
        plural = "" if len(corrections) == 1 else "s"
        proposal.warnings.append(
            f"Corrected {len(corrections)} entity link{plural} in the proposed note to "
            f"match entity-resolution's own answer: {'; '.join(corrections)}"
        )


# --------------------------------------------------------------------------
# DAG node 3: entity updates — the only place wakil edits an *existing* file.
# Scoped to compiled-truth-timeline-shaped entities: note-revision's own
# discipline (State vs. Timeline) doesn't define what "update" means for a
# single-occurrence note, so those are left exactly as reconciliation leaves
# them (correct links, no content change).

_H1_RE = re.compile(r"(?m)^#\s+.*$")
# SCHEMA.md's canonical heading is "## Timeline / Log", but a notable minority
# of real entity notes predate that convention and just say "## Timeline" —
# accept both rather than silently skipping updates to otherwise-well-formed
# notes (docs/TROUBLESHOOTING.md).
_TIMELINE_HEADING_RE = re.compile(r"(?m)^##\s+Timeline(?:\s*/\s*Log)?\s*$")


def _insert_timeline_entry(timeline_section: str, entry: str) -> str:
    """Prepend `entry` as the new first dated entry, right after the
    heading line — before every existing entry, including any
    auto-generated back-link bullets at the bottom, which are never
    touched."""
    newline_idx = timeline_section.find("\n")
    heading_line = timeline_section if newline_idx == -1 else timeline_section[:newline_idx]
    rest = "" if newline_idx == -1 else timeline_section[newline_idx:].lstrip("\n")
    entry = entry.strip()
    if not entry:
        return timeline_section
    return f"{heading_line}\n\n{entry}\n\n{rest}" if rest else f"{heading_line}\n\n{entry}\n"


_TRAILING_HR_RE = re.compile(r"\n*-{3,}\s*$")


def _split_note_sections(content: str) -> tuple[str, str, str] | None:
    """Locate an entity note body's three load-bearing pieces: the H1 line,
    the top (Compiled Truth) section, and the Timeline section — the same
    shape both `_merge_entity_note` (revision merges) and
    `prepare_entity_compile` (ADR 0016) need to identify before touching a
    note. `content` is the note body with frontmatter already stripped
    (e.g. `frontmatter.loads(...).content`).

    Returns None if the note doesn't have the expected H1 + '## Timeline /
    Log' shape — callers surface that as a warning/error rather than
    guessing at a different structure.
    """
    h1_match = _H1_RE.search(content)
    timeline_match = _TIMELINE_HEADING_RE.search(content)
    if h1_match is None or timeline_match is None or timeline_match.start() <= h1_match.end():
        return None

    h1_line = content[h1_match.start() : h1_match.end()]
    # Same convention callers re-apply when writing a new top section: the
    # "---" divider right before Timeline is not itself part of the
    # top-section content.
    top_section = _TRAILING_HR_RE.sub(
        "", content[h1_match.end() : timeline_match.start()]
    ).strip("\n")
    timeline_section = content[timeline_match.start() :]
    return h1_line, top_section, timeline_section


def _merge_entity_note(old_content: str, revision: EntityRevision, today: str) -> str | None:
    """Deterministic surgical merge — never a full-file regeneration (the
    "clobbering bug" note-revision's own skill warns against): existing
    frontmatter with only the delta keys changed, the H1 line preserved
    verbatim (slug consistency), everything between the H1 and the Timeline
    heading replaced with the re-synthesized compiled_truth, and one new
    entry prepended inside the Timeline section. Returns None if the note
    doesn't have the expected H1 + '## Timeline / Log' shape — the caller
    surfaces that as a warning rather than guessing at a different one.
    """
    try:
        post = frontmatter_lib.loads(old_content)
    except Exception:
        return None
    body = post.content

    sections = _split_note_sections(body)
    if sections is None:
        return None
    h1_line, old_top, timeline_section = sections

    metadata = dict(post.metadata)
    if revision.frontmatter_updates:
        metadata.update(revision.frontmatter_updates)
    metadata["updated"] = today

    # An empty/absent compiled_truth means "no change to the top section",
    # never "delete the top section" — has_update=True can legitimately mean
    # only the Timeline changed. Wiping existing State prose whenever the
    # model didn't re-send it is the exact clobbering bug this merge exists
    # to prevent (docs/TROUBLESHOOTING.md).
    compiled_truth = (revision.compiled_truth or "").strip() or old_top
    new_top = f"{h1_line}\n\n{compiled_truth}\n\n---" if compiled_truth else h1_line
    new_timeline = _insert_timeline_entry(timeline_section, revision.timeline_entry or "")

    new_body = f"{new_top}\n\n{new_timeline}".rstrip("\n") + "\n"
    frontmatter_yaml = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)
    return f"---\n{frontmatter_yaml}---\n\n{new_body}"


# Entities the resolution step judged not worth a full revision pass. A
# missing/None relevance is treated as inclusive by omission (falls through
# to the candidates list) rather than assumed low — see entity-resolve/SKILL.md.
_LOW_RELEVANCE = {"minor", "peripheral"}

# Caps worst-case fan-out from one truncating revision call to at most
# 2**_MAX_BISECTION_DEPTH sub-batches (8 at the default) — see ADR 0015.
_MAX_BISECTION_DEPTH = 3

_EntityCandidate = tuple[EntityResolution, Path, str]


def _run_entity_updates(
    config: WorkspaceConfig, client: ModelClient, text: str, proposal: EnrichmentProposal
) -> None:
    """Third model call: for each action=update resolution on a
    compiled-truth-timeline entity, decide whether it warrants a real
    content change and, if so, merge it. Degrades visibly — a failed call
    here doesn't block the already-validated create/stub-entity write path.
    """
    schemas = load_entity_schemas(config.root_path)
    candidates: list[tuple[EntityResolution, Path, str]] = []
    below_threshold: list[tuple[str, str]] = []
    for resolution in proposal.entity_resolutions:
        if resolution.action != "update" or not resolution.target_note_path:
            continue
        schema = schemas.get(resolution.entity_type)
        if schema is None or schema.page_shape != "compiled-truth-timeline":
            continue
        if resolution.relevance in _LOW_RELEVANCE:
            below_threshold.append((resolution.name, resolution.relevance))
            continue
        target = config.root_path / resolution.target_note_path
        if not target.is_file():
            proposal.warnings.append(
                f"{resolution.name}: entity resolution says update "
                f"{resolution.target_note_path}, but that file doesn't exist on disk — "
                "skipped"
            )
            continue
        try:
            content = target.read_text(encoding="utf-8")
        except OSError as exc:
            proposal.warnings.append(
                f"{resolution.name}: could not read {resolution.target_note_path}: {exc} — skipped"
            )
            continue
        candidates.append((resolution, target, content))

    if below_threshold:
        count = len(below_threshold)
        entity_word = "entity" if count == 1 else "entities"
        names = ", ".join(f"{name} ({relevance})" for name, relevance in below_threshold)
        proposal.warnings.append(
            f"{count} {entity_word} left untouched as below the relevance threshold: {names}"
        )

    if not candidates:
        return

    _revise_candidates(config, client, text, proposal, candidates)


def _split_candidates_by_content_length(
    candidates: list[_EntityCandidate],
) -> tuple[list[_EntityCandidate], list[_EntityCandidate]]:
    """Greedy 2-way balance by existing-note content length (longest-first):
    the single largest note is placed first, then each remaining candidate
    joins whichever half currently has less total content. Isolates the
    biggest cost driver from compounding with the next-biggest, rather than
    blind index-order halving — see ADR 0015, Decision 2 Step B."""
    ordered = sorted(candidates, key=lambda c: len(c[2]), reverse=True)
    half_a: list[_EntityCandidate] = []
    half_b: list[_EntityCandidate] = []
    size_a = size_b = 0
    for candidate in ordered:
        content_len = len(candidate[2])
        if size_a <= size_b:
            half_a.append(candidate)
            size_a += content_len
        else:
            half_b.append(candidate)
            size_b += content_len
    return half_a, half_b


def _apply_entity_revisions(
    proposal: EnrichmentProposal,
    candidates: list[_EntityCandidate],
    revisions: list[EntityRevision],
) -> None:
    today = datetime.now(UTC).date().isoformat()
    by_path = {res.target_note_path: content for res, _, content in candidates}
    for revision in revisions:
        old_content = by_path.get(revision.target_note_path)
        if old_content is None or not revision.has_update:
            continue
        new_content = _merge_entity_note(old_content, revision, today)
        if new_content is None:
            proposal.warnings.append(
                f"{revision.target_note_path}: doesn't match the expected H1 / "
                "'Timeline / Log' shape — update skipped, left unchanged"
            )
            continue
        if new_content == old_content:
            continue
        proposal.entity_updates.append(
            EntityUpdate(
                target_note_path=revision.target_note_path,
                old_content=old_content,
                new_content=new_content,
            )
        )


def _revise_candidates(
    config: WorkspaceConfig,
    client: ModelClient,
    text: str,
    proposal: EnrichmentProposal,
    candidates: list[_EntityCandidate],
    depth: int = 0,
) -> None:
    """Revise `candidates` in one call; on a truncated response, bisect by
    existing-note content length and retry each half, recursively, up to
    `_MAX_BISECTION_DEPTH` (ADR 0015, Decision 2 Step B). A validation
    failure (not a truncation) never triggers a split — it would recur
    identically on a smaller batch for an unrelated reason."""
    targets = [(res.target_note_path, content) for res, _, content in candidates]
    skill = load_skill("note-revision", config.root_path)
    system = build_system_prompt(skill, EntityRevisionOutput)
    cacheable_prefix, prompt = build_revision_prompt(
        text, proposal.summary, targets, context=proposal.context
    )
    try:
        result = complete_with_contract(
            client, system, prompt, EntityRevisionOutput, cacheable_prefix=cacheable_prefix
        )
    except ModelContractError as exc:
        if exc.truncated and len(candidates) > 1 and depth < _MAX_BISECTION_DEPTH:
            half_a, half_b = _split_candidates_by_content_length(candidates)
            _revise_candidates(config, client, text, proposal, half_a, depth + 1)
            _revise_candidates(config, client, text, proposal, half_b, depth + 1)
            return
        count = len(candidates)
        entity_word = "entity" if count == 1 else "entities"
        proposal.warnings.append(
            f"Entity updates failed while revising {count} {entity_word} in one call "
            f"({', '.join(res.name for res, _, _ in candidates)}); "
            f"existing notes left unchanged: {exc}"
        )
        return

    _apply_entity_revisions(proposal, candidates, result.revisions)


def _stub_content(metadata: dict, name: str) -> str:
    """Compiled Truth / Timeline skeleton per docs/entity-model.md."""
    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)
    return (
        f"---\n{frontmatter}---\n\n"
        f"# {name}\n\n"
        "## Compiled Truth\n\n"
        "_Synthesized current state — rewrite when facts change._\n\n"
        "## Open Threads\n\n"
        "---\n\n"
        "## Timeline / Log\n"
    )


def validate_proposal(
    proposal: EnrichmentProposal, kb_root: Path | None = None
) -> list[ProposalIssue]:
    """Invariant gate between prepare and apply; any issue blocks the write.

    Implements entity-resolution.md's constraints plus the schema check:
    every proposed new file must carry frontmatter valid against its entity
    schema; a type with no schema (or no canonical directory) is a hard stop,
    not a best-guess write; no two proposed files may share a path. Routing
    is 1:N by construction (proposed_note + stub_entities), and content-hash
    dedup is already enforced upstream at capture time.

    `kb_root` should be the workspace root so a kb-local schema override
    validates against the same schema extraction/entity-resolution used —
    omitted only by tests that don't exercise the override mechanism.
    """
    issues: list[ProposalIssue] = []
    schemas = load_entity_schemas(kb_root)

    proposed_files = list(proposal.stub_entities)
    if proposal.proposed_note is not None:
        proposed_files.insert(0, proposal.proposed_note)

    seen_paths: set[str] = set()
    for proposed in proposed_files:
        if proposed.path in seen_paths:
            issues.append(ProposalIssue(proposed.path, "duplicate proposed path"))
        seen_paths.add(proposed.path)
        try:
            metadata = frontmatter_lib.loads(proposed.content).metadata
        except Exception:
            metadata = {}
        entity_type = metadata.get("type") if isinstance(metadata, dict) else None
        if not isinstance(entity_type, str) or not entity_type:
            issues.append(ProposalIssue(proposed.path, "proposed file has no `type:` frontmatter"))
            continue
        for error in validate_frontmatter(entity_type, metadata, kb_root):
            issues.append(ProposalIssue(proposed.path, str(error)))

    # Edits to existing notes must still satisfy their type's schema —
    # frontmatter_updates could otherwise merge in a value that breaks it.
    for update in proposal.entity_updates:
        try:
            metadata = frontmatter_lib.loads(update.new_content).metadata
        except Exception:
            issues.append(
                ProposalIssue(update.target_note_path, "merged content is not valid frontmatter")
            )
            continue
        entity_type = metadata.get("type") if isinstance(metadata, dict) else None
        if not isinstance(entity_type, str) or not entity_type:
            issues.append(
                ProposalIssue(update.target_note_path, "merged file has no `type:` frontmatter")
            )
            continue
        for error in validate_frontmatter(entity_type, metadata, kb_root):
            issues.append(ProposalIssue(update.target_note_path, str(error)))

    # Creates that could not even build a stub: missing schema or directory.
    for resolution in proposal.entity_resolutions:
        if resolution.action != "create":
            continue
        schema = schemas.get(resolution.entity_type)
        if schema is None:
            issues.append(
                ProposalIssue(
                    f"entity:{resolution.name}",
                    f"no entity schema defines type '{resolution.entity_type}' "
                    f"(known: {', '.join(sorted(schemas))})",
                )
            )
        elif schema.directory is None:
            issues.append(
                ProposalIssue(
                    f"entity:{resolution.name}",
                    f"type '{resolution.entity_type}' has no canonical directory to "
                    "route a new page into",
                )
            )
    return issues


def apply_enrichment(config: WorkspaceConfig, proposal: EnrichmentProposal) -> EnrichmentResult:
    issues = validate_proposal(proposal, config.root_path)
    if issues:
        detail = "; ".join(str(issue) for issue in issues)
        raise IngestError(f"Proposal failed validation, nothing was written: {detail}")

    files_written: list[str] = []
    proposed_files = list(proposal.stub_entities)
    if proposal.proposed_note is not None:
        proposed_files.insert(0, proposal.proposed_note)
    for proposed in proposed_files:
        target = config.root_path / proposed.path
        if target.exists():
            raise IngestError(f"Refusing to overwrite existing file: {proposed.path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(proposed.content, encoding="utf-8")
        files_written.append(proposed.path)

    # Edits to existing notes: re-read immediately before writing and skip
    # (rather than overwrite blind) any file that changed since prepare —
    # mirrors schema_migrate_service.apply_migrations' stale-file guard.
    stale_updates_skipped: list[str] = []
    for update in proposal.entity_updates:
        target = config.root_path / update.target_note_path
        try:
            current = target.read_text(encoding="utf-8")
        except OSError as exc:
            stale_updates_skipped.append(f"{update.target_note_path}: unreadable ({exc})")
            continue
        if current != update.old_content:
            stale_updates_skipped.append(
                f"{update.target_note_path}: changed on disk since this enrichment was prepared"
            )
            continue
        target.write_text(update.new_content, encoding="utf-8")
        files_written.append(update.target_note_path)

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
                stance=candidate.stance,
                event_date=candidate.event_date,
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
        index_notes(session, workspace_id, config.root_path, prune=not config.is_linked_worktree)
        session.commit()

        return EnrichmentResult(
            source_id=source.id,
            ingest_run_id=run.id,
            files_written=files_written,
            memories_created=len(memory_rows),
            relationships_created=relationships_created,
            stale_updates_skipped=stale_updates_skipped,
        )


# --------------------------------------------------------------------------
# Entity compile pilot (docs/adr/0016) + bounded-size / full resynthesis
# (docs/adr/0017 Stages 1-2): `wakil entities compile SLUG`, a single-entity
# re-synthesis of Compiled Truth from an entity's own Timeline — no new
# source, no batching, no due-check. Reuses `_merge_entity_note` exactly
# as-is (a synthetic EntityRevision with no timeline_entry/
# frontmatter_updates) rather than a second merge path, for both the
# default additive mode and Stage 2's full-resynthesis mode.

# ADR 0017, Stage 1: target size for a compiled entity's top section (H1 +
# Compiled Truth). Anchored to query_service.py's NOTE_EXCERPT_CHARS = 2000
# — the window `wakil query`'s `_build_contexts` actually reads for a note
# — minus headroom for the frontmatter YAML and H1 line that share that
# same window ahead of Compiled Truth itself; 1400 sits inside the ADR's
# stated 1200-1500 range, calibrated against the two large, under-
# synthesized notes (companies/mosaic-private-markets.md,
# people/edward-bridges.md) that motivated this whole line of work, not
# against the vault broadly — most ordinary notes are expected to never
# approach it.
_COMPILED_TRUTH_TARGET_CHARS = 1400


def _resolve_entity_slug(config: WorkspaceConfig, slug: str) -> Path | None:
    """Search every entity type's canonical directory (per the schema
    catalog, not a hardcoded directory list) for `<slug>.md`, returning the
    first match. Directories are checked in a deterministic (sorted) order
    so the result doesn't depend on dict-iteration order of the schema
    catalog; only one page per slug is expected to exist in practice."""
    schemas = load_entity_schemas(config.root_path)
    directories = sorted({schema.directory for schema in schemas.values() if schema.directory})
    for directory in directories:
        candidate = config.root_path / directory / f"{slug}.md"
        if candidate.is_file():
            return candidate
    return None


def prepare_entity_compile(config: WorkspaceConfig, client: ModelClient, slug: str) -> EntityUpdate:
    """Prepare (but don't write) a compile of one entity's Compiled Truth.

    Resolves `slug` to a note, reads it, and hands its own Timeline to the
    `entity-compile` skill (docs/adr/0016) — an additive-only re-synthesis,
    not a merge against any new source. `ModelContractError` is left to
    propagate: this is a single-target CLI command, not a degrade-and-
    continue enrichment DAG step, so there is no partial result worth
    returning on failure.
    """
    target = _resolve_entity_slug(config, slug)
    if target is None:
        raise IngestError(f"No entity note found for slug '{slug}'.")

    old_content = target.read_text(encoding="utf-8")
    try:
        post = frontmatter_lib.loads(old_content)
    except Exception as exc:
        raise IngestError(f"{target}: could not parse frontmatter: {exc}") from exc

    sections = _split_note_sections(post.content)
    if sections is None:
        raise IngestError(
            f"{target}: doesn't match the expected H1 / 'Timeline / Log' shape — "
            "cannot compile"
        )
    h1_line, top_section, timeline_section = sections
    entity_name = h1_line.lstrip("#").strip() or slug

    skill = load_skill("entity-compile", config.root_path)
    system = build_system_prompt(skill, EntityCompileOutput)
    cacheable_prefix, prompt = build_compile_prompt(entity_name, top_section, timeline_section)
    result = complete_with_contract(
        client, system, prompt, EntityCompileOutput, cacheable_prefix=cacheable_prefix
    )

    target_note_path = str(target.relative_to(config.root_path))
    revision = EntityRevision(
        target_note_path=target_note_path,
        has_update=True,
        compiled_truth=result.compiled_truth,
        timeline_entry=None,
        frontmatter_updates=None,
    )
    today = datetime.now(UTC).date().isoformat()
    new_content = _merge_entity_note(old_content, revision, today)
    if new_content is None:
        # Shouldn't happen — _split_note_sections above already validated
        # the same shape _merge_entity_note requires — but never silently
        # fabricate a different result if it does.
        raise IngestError(f"{target}: merge failed unexpectedly after a successful compile call.")

    return EntityUpdate(
        target_note_path=target_note_path, old_content=old_content, new_content=new_content
    )


def prepare_entity_full_resynthesis(
    config: WorkspaceConfig, client: ModelClient, slug: str
) -> EntityUpdate:
    """Prepare (but don't write) a full resynthesis of one entity's Compiled
    Truth (`wakil entities compile SLUG --full`, docs/adr/0017 Stage 2).

    Mirrors `prepare_entity_compile`'s shape exactly — resolve slug, read
    the note, split its sections, load the skill, build the system prompt —
    with two differences: the user-content prompt comes from
    `build_full_resynthesis_prompt` (Timeline-only, no `top_section` passed
    at all, by ADR 0017's own design — see that function's docstring), and
    the result is allowed to drop redundant or ephemeral content the way
    additive mode never can. Loads the *same* `entity-compile` skill as the
    additive path — its "Full resynthesis mode" section supplies the
    different judgment; there is no second skill file. `ModelContractError`
    is left to propagate, same reasoning as `prepare_entity_compile`: a
    single-target CLI command has no partial result worth returning on
    failure.
    """
    target = _resolve_entity_slug(config, slug)
    if target is None:
        raise IngestError(f"No entity note found for slug '{slug}'.")

    old_content = target.read_text(encoding="utf-8")
    try:
        post = frontmatter_lib.loads(old_content)
    except Exception as exc:
        raise IngestError(f"{target}: could not parse frontmatter: {exc}") from exc

    sections = _split_note_sections(post.content)
    if sections is None:
        raise IngestError(
            f"{target}: doesn't match the expected H1 / 'Timeline / Log' shape — "
            "cannot compile"
        )
    h1_line, _top_section, timeline_section = sections
    entity_name = h1_line.lstrip("#").strip() or slug

    skill = load_skill("entity-compile", config.root_path)
    system = build_system_prompt(skill, EntityCompileOutput)
    cacheable_prefix, prompt = build_full_resynthesis_prompt(entity_name, timeline_section)
    result = complete_with_contract(
        client, system, prompt, EntityCompileOutput, cacheable_prefix=cacheable_prefix
    )
    if not result.compiled_truth.strip():
        # _merge_entity_note treats an empty compiled_truth as "no change" —
        # correct for prepare_entity_compile's additive mode, where that's a
        # real possible outcome, but full resynthesis is defined (ADR 0017)
        # to always produce a complete re-derivation. Letting an empty
        # response fall through that fallback would silently keep the old
        # Compiled Truth verbatim while the CLI still reports success.
        raise ModelContractError(
            contract="EntityCompileOutput",
            detail="full resynthesis returned an empty compiled_truth",
            truncated=False,
        )

    target_note_path = str(target.relative_to(config.root_path))
    revision = EntityRevision(
        target_note_path=target_note_path,
        has_update=True,
        compiled_truth=result.compiled_truth,
        timeline_entry=None,
        frontmatter_updates=None,
    )
    today = datetime.now(UTC).date().isoformat()
    new_content = _merge_entity_note(old_content, revision, today)
    if new_content is None:
        # Shouldn't happen — _split_note_sections above already validated
        # the same shape _merge_entity_note requires — but never silently
        # fabricate a different result if it does.
        raise IngestError(f"{target}: merge failed unexpectedly after a successful compile call.")

    return EntityUpdate(
        target_note_path=target_note_path, old_content=old_content, new_content=new_content
    )


def compiled_truth_text(content: str) -> str | None:
    """The top (Compiled Truth) section of a full note body — frontmatter
    parsed off, H1 line and Timeline section excluded — the same slice
    `_split_note_sections` isolates for `prepare_entity_compile` itself.
    Used by the CLI's Stage 1 size check (docs/adr/0017) both to measure
    against `_COMPILED_TRUTH_TARGET_CHARS` and, when the user chooses to
    hand-edit it, as the text handed to `click.edit()`. Returns None if
    `content` doesn't parse as frontmatter+body, or doesn't have the
    expected H1 + 'Timeline / Log' shape — the caller treats that as "can't
    judge size," not an error, since a `EntityUpdate` already produced by
    `_merge_entity_note` is expected to always have this shape.
    """
    try:
        post = frontmatter_lib.loads(content)
    except Exception:
        return None
    sections = _split_note_sections(post.content)
    if sections is None:
        return None
    return sections[1]


def rebuild_entity_update_with_compiled_truth(
    update: EntityUpdate, compiled_truth: str
) -> EntityUpdate | None:
    """Re-run the deterministic merge (docs/adr/0017, Stage 1's "Edit"
    choice) with `compiled_truth` — e.g. text a user hand-edited via
    `click.edit()` — standing in for the model's own output. Always merges
    against `update.old_content` (the true original on disk), never against
    `update.new_content`, which already has a compiled truth merged into it
    once. Returns None if the merge fails unexpectedly — shouldn't happen,
    since `update.old_content` already passed this same shape check once
    when `update` was first prepared, but never fabricate a result if it
    does.
    """
    revision = EntityRevision(
        target_note_path=update.target_note_path,
        has_update=True,
        compiled_truth=compiled_truth,
        timeline_entry=None,
        frontmatter_updates=None,
    )
    today = datetime.now(UTC).date().isoformat()
    new_content = _merge_entity_note(update.old_content, revision, today)
    if new_content is None:
        return None
    return EntityUpdate(
        target_note_path=update.target_note_path,
        old_content=update.old_content,
        new_content=new_content,
    )


def apply_entity_compile(config: WorkspaceConfig, update: EntityUpdate) -> bool:
    """Write a prepared compile, unless the target changed on disk since
    prepare — the same re-read-and-compare stale-guard `apply_enrichment`
    uses for entity updates. Returns True if written, False if skipped as
    stale (the caller reports accordingly)."""
    target = config.root_path / update.target_note_path
    try:
        current = target.read_text(encoding="utf-8")
    except OSError:
        return False
    if current != update.old_content:
        return False
    target.write_text(update.new_content, encoding="utf-8")
    return True


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


# Standalone ASR disfluency tokens (Whisper/parakeet-style transcribers emit
# these as isolated words). Stripping them is mechanical, not transcription
# repair: duplicated words, false starts, and grammar are left exactly as
# spoken — deciding what a stutter or garbled phrase "really meant" is
# judgment, and judgment belongs in a model-assisted step, not here.
_FILLER_WORD_RE = re.compile(r"\b(?:uh+|umm?|erm)\b,?", re.IGNORECASE)


def _strip_filler_words(text: str) -> str:
    text = _FILLER_WORD_RE.sub("", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text.strip()


# Seconds between the Whisper/Apple "reference date" epoch (2001-01-01 UTC,
# the same epoch macOS uses for NSDate/CFAbsoluteTime) and a given timestamp.
_WHISPER_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)


def _whisper_recorded_at(raw_seconds: object) -> str | None:
    if not isinstance(raw_seconds, int | float):
        return None
    try:
        return (_WHISPER_EPOCH + timedelta(seconds=raw_seconds)).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _read_whisper_metadata(file: Path) -> dict:
    try:
        with zipfile.ZipFile(file) as archive, archive.open("metadata.json") as fh:
            return json.load(fh)
    except zipfile.BadZipFile as exc:
        raise IngestError(f"{file} is not a valid whisper archive (expected a zip): {exc}") from exc
    except KeyError as exc:
        raise IngestError(f"{file} has no metadata.json inside the archive") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise IngestError(f"Could not read whisper metadata from {file}: {exc}") from exc


def _dialogue_from_segments(segments: list[dict], speaker_of: Callable[[dict], str]) -> str:
    """Merge diarized segments into `**Speaker**: turn` dialogue text.

    Shared by every diarized JSON transcript shape wakil recognizes:
    consecutive segments from the same speaker are merged into one turn, and
    standalone ASR filler tokens are stripped (see `_strip_filler_words`) --
    otherwise the text is kept verbatim. `speaker_of` isolates the one thing
    that varies between shapes (where/how the speaker label is stored).
    """
    turns: list[tuple[str, list[str]]] = []
    for segment in sorted(segments, key=lambda s: s.get("start", 0)):
        text = _strip_filler_words(str(segment.get("text", "")).strip())
        if not text:
            continue
        speaker = speaker_of(segment).strip() or "Unknown speaker"
        if turns and turns[-1][0] == speaker:
            turns[-1][1].append(text)
        else:
            turns.append((speaker, [text]))
    return "\n\n".join(f"**{speaker}**: {' '.join(parts)}" for speaker, parts in turns)


def parse_whisper_transcript(file: Path) -> tuple[str, str | None]:
    """Extract speaker-labeled dialogue from an Apple-style .whisper archive.

    A `.whisper` file is a zip containing `metadata.json`: diarized
    `transcripts` segments (speaker name, start/end ms, text) plus a
    `speakers` roster and capture timestamps.

    Returns (dialogue_text, recorded_at) where recorded_at is an ISO date
    derived from the archive's own capture timestamp, or None if absent.
    """
    data = _read_whisper_metadata(file)
    segments = data.get("transcripts")
    if not isinstance(segments, list) or not segments:
        raise IngestError(f"{file}: metadata.json has no transcript segments")

    dialogue = _dialogue_from_segments(
        segments, lambda s: str((s.get("speaker") or {}).get("name") or "")
    )
    if not dialogue:
        raise IngestError(f"{file}: transcript segments contained no text")

    recorded_at = _whisper_recorded_at(data.get("dateCreated"))
    return dialogue, recorded_at


def parse_json_transcript(file: Path) -> str:
    """Extract speaker-labeled dialogue from a plain-JSON transcript export.

    A different diarized shape from the `.whisper` archive above: an
    unwrapped `.json` file (no zip, no `speakers`/capture-timestamp
    metadata) with a top-level `segments` array, each item's `speaker` a
    plain string rather than a `{"name": ...}` object. Handled identically
    otherwise — merged into speaker turns via `_dialogue_from_segments`.
    """
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IngestError(f"Could not read JSON transcript {file}: {exc}") from exc

    segments = data.get("segments") if isinstance(data, dict) else None
    if not isinstance(segments, list) or not segments:
        raise IngestError(f"{file}: no `segments` array found in JSON transcript")

    dialogue = _dialogue_from_segments(segments, lambda s: str(s.get("speaker") or ""))
    if not dialogue:
        raise IngestError(f"{file}: transcript segments contained no text")
    return dialogue


_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_COMPACT_DATE_RE = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)")
# A leading date prefix on a source filename (e.g. "2026-07-16-call") — kept
# out of both the derived title and the note slug, since the date already
# lives in frontmatter/the note's own date-prefixed filename.
_LEADING_DATE_RE = re.compile(r"^\d{4}-?\d{2}-?\d{2}-?")


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


def _relative_origin(config: WorkspaceConfig, file: Path) -> str:
    """POSIX path from the KB root, so DB rows, prompts, and frontmatter never
    leak a machine-specific absolute path. Falls back to the absolute path
    only when the source file lives outside the workspace entirely.
    """
    try:
        return file.resolve().relative_to(config.root_path.resolve()).as_posix()
    except ValueError:
        return file.as_posix()


# --------------------------------------------------------------------------
# Frontmatter and workspace guidance


def load_workspace_guides(config: WorkspaceConfig) -> dict[str, str]:
    """RESOLVER.md (routing) excerpt, when present.

    Page shape and metadata guidance no longer comes from a workspace
    document — `describe_entity_types_full`/`describe_entity_types`
    (`wakil.llm.prompts`) already render that structurally from
    `load_entity_schemas()`. Subject-matter routing has no code-owned
    equivalent, so RESOLVER.md stays the sole authority for it.
    """
    guides = {}
    path = config.root_path / "RESOLVER.md"
    if path.is_file():
        with contextlib.suppress(OSError):
            guides["RESOLVER.md"] = path.read_text(encoding="utf-8", errors="replace")[
                :GUIDE_MAX_CHARS
            ]
    return guides


def transcript_frontmatter_template(config: WorkspaceConfig) -> dict | None:
    """A frontmatter field template for transcript raw captures.

    Derived from the `source` entity schema (`schema/entities/source.yaml`):
    its base fields plus its `transcript` origin sub-schema — the same
    effective-fields merge `wakil.schema.validate.validate_frontmatter` does
    for an `origin: transcript` note. Field values are placeholders (`""`,
    or the schema's own `type` for the `type` key); `_transcript_metadata`
    fills in the ones it knows a real value for. Returns None only if the
    resolved schema catalog (kb-local/user/built-in) defines no `source`
    type at all.
    """
    schema = load_entity_schemas(config.root_path).get("source")
    if schema is None:
        return None
    field_names = ["type", *schema.fields, *schema.origins.get("transcript", {})]
    return {name: (schema.type if name == "type" else "") for name in field_names}


# Template keys we know how to fill, in normalized form.
_KNOWN_FIELD_VALUES = {
    "created": "created",
    "captured": "created",  # source.yaml's own field name for this concept
    "create_date": "created",
    "date_created": "created",
    "meeting_date": "meeting_date",
    "date": "meeting_date",
    "title": "title",
    "name": "title",
    "abstract": "abstract",
    # "origin" is conventionally an enumerated kind (transcript/article/...),
    # not a path — the path/URL goes in "url"/"source_file" as a `file:` ref.
    "origin": "origin_kind",
    "url": "file_url",
    "source_file": "file_url",
    "context": "context",
}


def _build_raw_file(
    config: WorkspaceConfig, proposal: CaptureProposal, slug_source: str
) -> ProposedFile:
    """slug_source is the deterministic filename/article-scrape basis for the
    raw file's path (docs/adr/0010) -- kept independent of proposal.title,
    which is model-generated and would otherwise make the raw file's path
    non-deterministic and break capture's idempotent-by-content-hash dedup
    across identical re-ingests.
    """
    created = datetime.now(UTC).date().isoformat()
    directory = Path(config.ingest_directory) / RAW_DIRS.get(proposal.source_type, "clippings")
    slug = slugify(slug_source)
    # Avoid a doubled date when the source filename already carried one.
    slug = _LEADING_DATE_RE.sub("", slug) or "untitled"
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
        if proposal.abstract:
            metadata["abstract"] = proposal.abstract
        if proposal.context:
            metadata["context"] = proposal.context

    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)
    body = proposal.text
    if proposal.source_type == "transcript":
        body = f"# {path.stem}\n\n{body}"
    return ProposedFile(path=str(path), content=f"---\n{frontmatter}---\n\n" + body + "\n")


def _transcript_metadata(config: WorkspaceConfig, proposal: CaptureProposal, created: str) -> dict:
    values = {
        "created": created,
        "meeting_date": proposal.meeting_date,
        "title": proposal.title,
        "abstract": proposal.abstract,
        "origin_kind": proposal.source_type,
        "file_url": f"file:{proposal.origin}",
        "context": proposal.context,
    }
    template = transcript_frontmatter_template(config)
    if template is None:
        # No `source` schema resolved at all (a broken override): fall back
        # to the two fields every transcript capture needs regardless.
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
        select(Workspace.id).where(Workspace.root_path == str(config.state_root))
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
