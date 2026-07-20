"""Ingest raw sources into the knowledge base — in two separate steps.

Step 1, capture (`wakil ingest ...`): deterministic only, no model. The
source text is extracted (transcripts get light cleanup), deduped by content
hash, and written under sources/ as a raw capture with frontmatter shaped by
the KB's SCHEMA.md when it defines a template for transcripts/sources —
otherwise transcripts get exactly two fields: create date and meeting date.

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

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import frontmatter as frontmatter_lib
import yaml
from sqlalchemy import or_, select

from wakil.app.search_service import SearchHit, search_workspace
from wakil.app.workspace_service import index_notes, open_session
from wakil.config.settings import WorkspaceConfig
from wakil.integrations.web import fetch_article
from wakil.llm.client import ModelClient
from wakil.llm.prompts import (
    build_extraction_prompt,
    build_resolution_prompt,
    build_revision_prompt,
)
from wakil.llm.schemas import (
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
    meeting_date: str | None = None
    duplicate_of: int | None = None


@dataclass
class CaptureResult:
    source_id: int
    ingest_run_id: int
    raw_file_path: str


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
    *,
    file: Path | None = None,
    url: str | None = None,
    context: str | None = None,
) -> CaptureProposal:
    meeting_date: str | None = None
    if kind in ("transcript", "text"):
        if file is None:
            raise IngestError(f"{kind} ingest needs a file path")
        if kind == "transcript" and file.suffix.lower() == ".whisper":
            text, recorded_at = parse_whisper_transcript(file)
            meeting_date = infer_meeting_date(file, text) or recorded_at
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
        title = stem.replace("-", " ").replace("_", " ").strip() or file.name
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
        index_notes(session, workspace_id, config.root_path, prune=not config.is_linked_worktree)
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

        # Relevance search optimizes for "notes that talk about X" and reliably
        # buries a short entity stub (title literally "Mosaic") behind longer
        # notes that just mention the name often. Entity resolution needs the
        # opposite question answered — "does a page named X already exist" —
        # so supplement with a direct title lookup against known entity
        # directories, independent of QMD/FTS ranking.
        workspace_id, _ = _require_workspace_ids(session, config)
        seen_paths = {hit.ref for hit in related_notes}
        for path, note_title in _candidate_entity_notes(
            session,
            workspace_id,
            " ".join(filter(None, [context, text])),
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
# Single capitalized words that are only candidates because they happened to
# start a sentence — common enough in free-text context strings that they'd
# otherwise pollute every lookup with irrelevant title matches.
_PROPER_NOUN_STOPWORDS = {
    "I",
    "The",
    "A",
    "An",
    "Meeting",
    "Call",
    "Phone",
    "Yeah",
    "Well",
    "So",
    "But",
    "And",
    "This",
    "That",
    "We",
    "She",
    "He",
    "They",
}


# Generic words in a humanized filename/title ("offer", "sync", "call") that
# would otherwise substring-match unrelated entity notes once casing is no
# longer a filter (see _title_terms).
_TITLE_TERM_STOPWORDS = {w.lower() for w in _PROPER_NOUN_STOPWORDS} | {
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
        if len(phrase) > 2 and phrase not in _PROPER_NOUN_STOPWORDS
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
    stub here — validate_proposal() reports them as hard stops instead of
    best-guessing a location or frontmatter shape.
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
            continue  # surfaced by validate_proposal
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


# Wikilink form per note-conformance/SKILL.md: [[path]] or [[path|display]].
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


def _deslug(path: str) -> str:
    """Comparable text for a wikilink with no `|display` part: its slug, deslugged."""
    stem = path.strip().rsplit("/", 1)[-1]
    if stem.endswith(".md"):
        stem = stem[:-3]
    return stem.replace("-", " ").replace("_", " ")


def _normalize_link_path(path: str) -> str:
    """A path with any trailing `.md` stripped, for same-target comparison.

    This KB's own wikilinks mix both conventions (`[[people/x]]` and
    `[[sources/y.md]]` both appear for real, valid links) — extraction's
    `.md`-less link and entity-resolution's `target_note_path` (always
    `.md`, matching `Note.path`) can refer to the identical page while
    differing only by this suffix. Only rewrite a link when it points at a
    genuinely different entity, never just to change its extension style.
    """
    stripped = path.strip()
    return stripped[:-3] if stripped.endswith(".md") else stripped


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
_TIMELINE_HEADING_RE = re.compile(r"(?m)^##\s+Timeline\s*/\s*Log\s*$")


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

    h1_match = _H1_RE.search(body)
    timeline_match = _TIMELINE_HEADING_RE.search(body)
    if h1_match is None or timeline_match is None or timeline_match.start() <= h1_match.end():
        return None

    h1_line = body[h1_match.start() : h1_match.end()]
    timeline_section = body[timeline_match.start() :]

    metadata = dict(post.metadata)
    if revision.frontmatter_updates:
        metadata.update(revision.frontmatter_updates)
    if "updated" in metadata:
        metadata["updated"] = today

    compiled_truth = (revision.compiled_truth or "").strip()
    new_top = f"{h1_line}\n\n{compiled_truth}\n\n---" if compiled_truth else h1_line
    new_timeline = _insert_timeline_entry(timeline_section, revision.timeline_entry or "")

    new_body = f"{new_top}\n\n{new_timeline}".rstrip("\n") + "\n"
    frontmatter_yaml = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)
    return f"---\n{frontmatter_yaml}---\n\n{new_body}"


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
    for resolution in proposal.entity_resolutions:
        if resolution.action != "update" or not resolution.target_note_path:
            continue
        schema = schemas.get(resolution.entity_type)
        if schema is None or schema.page_shape != "compiled-truth-timeline":
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

    if not candidates:
        return

    targets = [(res.target_note_path, content) for res, _, content in candidates]
    skill = load_skill("note-revision", config.root_path)
    system = build_system_prompt(skill, EntityRevisionOutput)
    prompt = build_revision_prompt(text, proposal.summary, targets, context=proposal.context)
    try:
        result = complete_with_contract(client, system, prompt, EntityRevisionOutput)
    except ModelContractError as exc:
        proposal.warnings.append(f"Entity updates failed; existing notes left unchanged: {exc}")
        return

    today = datetime.now(UTC).date().isoformat()
    by_path = {res.target_note_path: content for res, _, content in candidates}
    for revision in result.revisions:
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


def parse_whisper_transcript(file: Path) -> tuple[str, str | None]:
    """Extract speaker-labeled dialogue from an Apple-style .whisper archive.

    A `.whisper` file is a zip containing `metadata.json`: diarized
    `transcripts` segments (speaker name, start/end ms, text) plus a
    `speakers` roster and capture timestamps. Consecutive segments from the
    same speaker are merged into one turn, and standalone ASR filler tokens
    are stripped (see `_strip_filler_words`) — otherwise the text is kept
    verbatim.

    Returns (dialogue_text, recorded_at) where recorded_at is an ISO date
    derived from the archive's own capture timestamp, or None if absent.
    """
    data = _read_whisper_metadata(file)
    segments = data.get("transcripts")
    if not isinstance(segments, list) or not segments:
        raise IngestError(f"{file}: metadata.json has no transcript segments")

    turns: list[tuple[str, list[str]]] = []
    for segment in sorted(segments, key=lambda s: s.get("start", 0)):
        text = _strip_filler_words(str(segment.get("text", "")).strip())
        if not text:
            continue
        speaker = str((segment.get("speaker") or {}).get("name") or "").strip() or "Unknown speaker"
        if turns and turns[-1][0] == speaker:
            turns[-1][1].append(text)
        else:
            turns.append((speaker, [text]))

    if not turns:
        raise IngestError(f"{file}: transcript segments contained no text")

    dialogue = "\n\n".join(f"**{speaker}**: {' '.join(parts)}" for speaker, parts in turns)
    recorded_at = _whisper_recorded_at(data.get("dateCreated"))
    return dialogue, recorded_at


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
    # "origin" is conventionally an enumerated kind (transcript/article/...),
    # not a path — the path/URL goes in "url"/"source_file" as a `file:` ref.
    "origin": "origin_kind",
    "url": "file_url",
    "source_file": "file_url",
    "context": "context",
}


def _build_raw_file(config: WorkspaceConfig, proposal: CaptureProposal) -> ProposedFile:
    created = datetime.now(UTC).date().isoformat()
    directory = Path(config.ingest_directory) / RAW_DIRS.get(proposal.source_type, "clippings")
    slug = slugify(proposal.title)
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
        "origin_kind": proposal.source_type,
        "file_url": f"file:{proposal.origin}",
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
