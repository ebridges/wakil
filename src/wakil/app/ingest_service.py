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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PurePosixPath

import frontmatter as frontmatter_lib
import yaml
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from wakil.app.search_service import SearchHit, search_workspace
from wakil.app.workspace_service import SourceRelink, index_notes, open_session
from wakil.config.settings import WorkspaceConfig, workspace_date, workspace_today
from wakil.integrations import git
from wakil.integrations.web import fetch_article
from wakil.knowledge.markdown import SKIPPED_DIRS
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
from wakil.schema.loader import EntitySchema, load_entity_schemas, resolve_page_shape_template
from wakil.schema.validate import validate_frontmatter
from wakil.storage.schema import (
    EnrichmentCheckpoint,
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


class MissingUpdateTargetsError(IngestError):
    """Enrichment produced no file writes because every target it resolved
    lives on a branch that isn't merged into this working tree (#188).

    Raised before anything is persisted so the run really is a no-op: the
    remediation is to make those pages reachable and re-run, and a
    partially-applied run would make that re-run duplicate every candidate
    memory it had already written. Leaving the source `raw` is also what
    keeps the re-run from needing `--force`, which would clear the phase
    checkpoints. Carries the targets structured so the CLI can render them
    and the MCP layer can relay them."""

    def __init__(self, targets: list["_MissingUpdateTarget"]) -> None:
        self.targets = targets
        detail = "; ".join(
            f"{t.name} -> {t.path}" + (f" (on {', '.join(t.branches)})" if t.branches else "")
            for t in targets
        )
        super().__init__(
            "Nothing was written for this source. Entity resolution resolved targets "
            f"that aren't in the working tree: {detail}"
        )


@dataclass
class ProposedFile:
    """A new file to write. `confidence` mirrors `EntityUpdate.confidence` —
    populated for a stub built from an action=create entity resolution
    (`EntityResolution.proposed_frontmatter_confidence`) or for extraction's
    own proposed_note (`ProposedNoteModel.frontmatter_confidence`), so a
    thinly-supported frontmatter guess (e.g. a book's `status` inferred from
    one early highlight) can be flagged in the enrich preview instead of
    rendering identically to a well-supported create. None everywhere else
    (raw captures), where the concept doesn't apply."""

    path: str  # workspace-relative
    content: str
    confidence: float | None = None


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
    # Frontmatter/H1 the input file already carried (#172). Authored fields
    # win over wakil's generated ones in `_build_raw_file`, and an authored
    # H1 suppresses the generated one so the note is never double-wrapped.
    authored_metadata: dict = field(default_factory=dict)
    authored_h1: str | None = None
    # Authored values wakil declined to use (a wakil-owned key, or one the
    # `source` schema rejects). Shown in the preview -- silently dropping a
    # value the author wrote is exactly the kind of invisible behaviour #172
    # was about.
    warnings: list[str] = field(default_factory=list)
    # Set when the computed destination is already occupied (#173). Capture
    # used to silently pick `<name>-1.md` instead, producing a near-duplicate
    # nobody noticed. Surfaced in the preview and refused at apply time.
    collision: str | None = None
    # Set when an existing Source row already claims the computed destination.
    # `--overwrite` replaces the file but cannot rehome that row, so the two
    # would end up sharing one `raw_text_path` — see `apply_capture`.
    collision_source_id: int | None = None
    overwrite: bool = False


@dataclass
class CaptureResult:
    source_id: int
    ingest_run_id: int
    raw_file_path: str
    # True when the write replaced an existing file rather than creating one,
    # so the result line reports a destructive write as one (#173).
    replaced: bool = False
    # Renamed raw captures this call's indexing repointed. Carried out to the
    # caller because `wakil ingest`/`wakil enrich` index too, and a repoint
    # applied with no output is the silent pointer change working agreement
    # item 12 rules out — `wakil index` was the only path that reported it.
    sources_relinked: list["SourceRelink"] = field(default_factory=list)


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
    schema_migrate_service's stale-file guard).

    `confidence` mirrors `EntityRevision.confidence` (how well-supported the
    revision's content is, not whether it was warranted at all) — carried
    through so the enrichment preview can flag a thinly-supported update
    instead of rendering it identically to a well-supported one. None for
    updates produced outside a model revision call (e.g. `wakil entities
    compile`), where the concept doesn't apply."""

    target_note_path: str
    old_content: str
    new_content: str
    confidence: float | None = None


@dataclass
class _MissingUpdateTarget:
    """An `action=update` resolution whose target isn't in the working tree.

    Kept structured rather than folded into `warnings` so the CLI/MCP layer
    can decide the exit code: a run that produced nothing *because* its
    targets live on an unmerged branch is a failure, not a quiet success
    (#188)."""

    name: str
    path: str
    branches: list[str] = field(default_factory=list)


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
    # The source's own captured/retrieved date (yyyy-mm-dd), used as a
    # fallback Timeline heading when the model's generated heading doesn't
    # carry a real date of its own (issue #77) — never left as a
    # placeholder like "(date not recorded)" in an append-only Timeline.
    source_captured_date: str | None = None
    # Update targets entity resolution asked for that the working tree
    # doesn't have -- typically because an earlier, unmerged ingest branch
    # created them (#188).
    missing_update_targets: list[_MissingUpdateTarget] = field(default_factory=list)


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
    # See CaptureResult.sources_relinked.
    sources_relinked: list["SourceRelink"] = field(default_factory=list)


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
    parsed: ParsedInput | None = None
    legacy_text: str | None = None
    if kind in ("transcript", "text"):
        if file is None:
            raise IngestError(f"{kind} ingest needs a file path")
        if kind == "transcript" and file.suffix.lower() == ".whisper":
            text, recorded_at = parse_whisper_transcript(file, config)
            meeting_date = infer_meeting_date(file, text) or recorded_at
        elif kind == "transcript" and file.suffix.lower() == ".json":
            text = parse_json_transcript(file)
            meeting_date = infer_meeting_date(file, text)
        else:
            try:
                raw = file.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise IngestError(f"Could not read {file}: {exc}") from exc
            if file.suffix.lower() == ".srt":
                text = strip_srt(raw)
                legacy_source = text
            elif file.suffix.lower() == ".md":
                # Only `.md` can meaningfully carry frontmatter/an H1; a
                # `.txt`/`.srt` dump is machine output either way.
                parsed = _split_authored_markdown(raw)
                text = parsed.body
                # Pre-#172 there was no frontmatter split, so the cleaner saw
                # the whole file.
                legacy_source = raw
            else:
                text = raw
                legacy_source = raw
            if kind == "transcript":
                # An authored `.md` is a human artifact whose `**[00:36]**`
                # markers are deliberate -- cleaning it deletes real content
                # (#179). A bare dump still needs the timestamp/whitespace
                # pass.
                if parsed is None or not parsed.is_authored_markdown:
                    text = clean_transcript(text)
                # What this same input hashed to before this change, for the
                # dedup check below. Computed for *every* transcript file
                # type, not just authored `.md`: #179 moved the cleaner's
                # bracket pattern, so a `.txt`/`.srt` export using `**[00:36]**`
                # turn labels also hashes differently now than when it was
                # first captured.
                legacy_text = clean_transcript(
                    legacy_source, bracket_re=_LEGACY_BRACKET_TS_RE
                )
                meeting_date = infer_meeting_date(file, text)
            elif parsed is not None:
                legacy_text = raw
            if parsed is not None:
                meeting_date = _authored_meeting_date(parsed) or meeting_date
        origin = _relative_origin(config, file)
        # Strip a leading date off the origin filename's own stem (falling
        # back to the unstripped stem when the filename is nothing *but* a
        # date, e.g. "2014-02-17.md" -- see _build_raw_file, which must not
        # repeat this strip, for why that fallback matters).
        stem = _LEADING_DATE_RE.sub("", file.stem) or file.stem
        # Deterministic basis for the raw file's slug only -- see below,
        # this is intentionally never overwritten by the model's title. An
        # authored title/H1 is preferred over the (often scratch-pad)
        # filename, and is just as deterministic (#172).
        slug_source = (
            (_authored_slug_source(parsed) if parsed is not None else None)
            or stem.replace("-", " ").replace("_", " ").strip()
            or file.name
        )
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

    # What the pre-#172 code would have hashed for this same input, so an
    # already-ingested file is still recognised. None when the basis is
    # unchanged (non-`.md`, or a `.md` with nothing stripped).
    legacy_hash = (
        hashlib.sha256(legacy_text.encode()).hexdigest()
        if legacy_text is not None and legacy_text != text
        else None
    )

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
        authored_metadata=parsed.metadata if parsed is not None else {},
        authored_h1=parsed.h1 if parsed is not None else None,
    )
    if parsed is not None and parsed.warning is not None:
        proposal.warnings.append(parsed.warning)

    with open_session(config) as session:
        # Also match the pre-#172 hash basis. That basis was the *raw* file
        # including its own frontmatter; it is now the body alone, so without
        # this every `.md` a user already ingested would re-ingest as a new
        # source -- and `apply_capture`'s overwrite guard wouldn't catch it
        # either, since the destination slug now comes from the authored title
        # rather than the basename. Exactly the population #172 affected.
        candidates = [content_hash]
        if legacy_hash is not None and legacy_hash != content_hash:
            candidates.append(legacy_hash)
        # Archived rows don't block a re-capture: archiving a bad attempt so
        # the redo can land is the workflow #183 exists for, and blocking on
        # a row the user just declared dead is the same dead end the path
        # collision had.
        existing = session.scalar(
            select(Source.id).where(
                Source.content_hash.in_(candidates), Source.archived_at.is_(None)
            )
        )
        if existing is not None:
            proposal.duplicate_of = existing
            return proposal

    # An authored title/abstract is the user's own, so the model's doesn't
    # replace it (working agreement item 12) -- and when the file supplies
    # both, the capture-time call (ADR 0010) has nothing left to contribute,
    # so don't pay for it at all.
    authored_title = _authored_text(proposal.authored_metadata, "title", "name")
    authored_abstract = _authored_text(proposal.authored_metadata, "abstract")
    if authored_title and authored_abstract:
        proposal.title, proposal.abstract = authored_title, authored_abstract
    else:
        metadata = _generate_capture_metadata(config, client, kind, origin, text, context)
        proposal.title = authored_title or metadata.title
        # Keep the DB row and the file's frontmatter agreeing: `_build_raw_file`
        # would otherwise write the authored abstract while `Source` kept the
        # model's, and the divergence is invisible until search disagrees with
        # the note.
        proposal.abstract = authored_abstract or metadata.abstract
    proposal.raw_file = _build_raw_file(config, proposal, slug_source)
    proposal.collision_source_id = _source_owning_path(config, proposal.raw_file.path)
    return proposal


def _authored_text(metadata: dict, *keys: str) -> str | None:
    """The first non-empty string among `keys` in an input's own frontmatter."""
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _source_owning_path(
    config: WorkspaceConfig, path: str, *, exclude: int | None = None
) -> int | None:
    """The id of the *live* source whose raw capture lives at `path`, if any.

    Archived rows are skipped, which is what makes `wakil sources archive` the
    escape hatch the collision error tells the user to reach for (#183) —
    without the filter, following that instruction hit the identical error
    again with nothing left to try.
    """
    with open_session(config) as session:
        query = select(Source.id).where(
            Source.raw_text_path == path, Source.archived_at.is_(None)
        )
        if exclude is not None:
            query = query.where(Source.id != exclude)
        return session.scalar(query)


def _generate_capture_metadata(
    config: WorkspaceConfig,
    client: ModelClient,
    source_type: str,
    origin: str,
    text: str,
    context: str | None,
) -> CaptureMetadata:
    """The one model call capture makes (docs/adr/0010): title + abstract,
    grounded in the captured text itself rather than just the filename."""
    today = workspace_today(config)
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
    replacing = target.exists()
    # Owned-path first, deliberately: --overwrite replaces the file but cannot
    # rehome the Source row that points at it, and two rows sharing one
    # `raw_text_path` means `wakil enrich <old id>` reads the *new* text and
    # files its memories under the old source (#173). Checking `exists()` first
    # told the user to re-run with --overwrite and the re-run then said
    # --overwrite won't help — same precedence `print_capture_proposal` uses.
    owner_id = _source_owning_path(config, proposal.raw_file.path)
    if owner_id is not None:
        raise IngestError(
            f"{proposal.raw_file.path} is already the raw capture of source #{owner_id} "
            f"(see `wakil sources show {owner_id}`). Writing there would leave two "
            f"sources pointing at one file, so `--overwrite` won't clear this. If this "
            f"capture supersedes that one, archive it — `wakil sources archive "
            f"{owner_id} --superseded-by <new id>` — which frees the path; otherwise "
            f"rename the input so it lands somewhere else."
        )
    if replacing and not proposal.overwrite:
        raise IngestError(
            f"{proposal.raw_file.path} already exists. Re-run with --overwrite to replace "
            f"it, or point at a different input. (If this is the same recording captured "
            f"twice, check `wakil sources list` first.)"
        )

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
            # Only roll back a file this call created. Under --overwrite the
            # write replaced content that was already on disk, and deleting it
            # would turn a lost race into data loss.
            if not replacing:
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
        indexed = index_notes(
            session, workspace_id, config.root_path, prune=not config.is_linked_worktree
        )
        session.commit()
        return CaptureResult(
            source_id=source.id,
            ingest_run_id=run.id,
            raw_file_path=proposal.raw_file.path,
            replaced=replacing,
            sources_relinked=indexed.sources_relinked,
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
        # Archived rows are excluded: archiving means "stop spending attention
        # here", and a backfill is one paid model call per source.
        sources = session.scalars(
            select(Source).where(Source.archived_at.is_(None)).order_by(Source.id)
        ).all()
        for source in sources:
            metadata = json.loads(source.metadata_json or "{}")
            if metadata.get("abstract") or not source.raw_text_path:
                continue
            try:
                text = _load_source_text(config, source)
            except IngestError:
                continue
            generated = _generate_capture_metadata(
                config,
                client,
                source.source_type,
                source.origin or "",
                text,
                metadata.get("context"),
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
    archived_at: datetime | None = None
    archive_reason: str | None = None
    superseded_by_id: int | None = None


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
        archived_at=row.archived_at,
        archive_reason=row.archive_reason,
        superseded_by_id=row.superseded_by_id,
    )


def list_sources(
    config: WorkspaceConfig,
    status: str | None = None,
    limit: int | None = 50,
    include_archived: bool = False,
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
        if not include_archived:
            stmt = stmt.where(Source.archived_at.is_(None))
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


def relink_source(config: WorkspaceConfig, source_id: int, new_path: str) -> SourceSummary:
    """Point a source at its raw capture's current path.

    `wakil index` notices when a markdown file moves and updates the `notes`
    table, but nothing propagated that to `sources.raw_text_path`, so a
    renamed capture left `enrich` failing with "Could not read raw capture
    <old path>" and no supported way to fix it (#178).

    `new_path` is caller-supplied — an argument on the CLI, a parameter on the
    `sources_relink` MCP tool — and everything downstream treats
    `raw_text_path` as workspace-relative and trusted: `enrich` reads the file
    and puts its contents in a model prompt. So it is confined to the
    workspace here, the same bar `_sanitize_note` holds model-proposed note
    paths to, rather than accepting any path on the machine.
    """
    root = config.root_path.resolve()
    target = (root / new_path).resolve()
    if not target.is_relative_to(root):
        raise IngestError(
            f"{new_path} is outside the knowledge base ({root}). "
            "A source's raw capture has to live in the workspace."
        )
    # Inside the workspace isn't enough: `.git/` and `.wakil/` are inside it,
    # and `.wakil/` holds the config and the SQLite database. `enrich` feeds
    # `raw_text_path` straight into a model prompt, and this tool is
    # agent-callable over MCP. Same exclusion `discover_markdown_files` uses.
    relative_parts = target.relative_to(root).parts
    if any(part.startswith(".") or part in SKIPPED_DIRS for part in relative_parts[:-1]):
        raise IngestError(
            f"{new_path} is inside a directory wakil doesn't treat as knowledge base "
            "content. A source's raw capture has to be a note, not tooling state."
        )
    if not target.is_file():
        raise IngestError(f"No file at {new_path} — nothing to relink to.")
    # Store the workspace-relative form, so `../kb/sources/x.md` and a symlinked
    # route to the same file both land as the one path everything else expects.
    relative = target.relative_to(root).as_posix()
    # The same one-source-per-path invariant `apply_capture` enforces. Without
    # it, relink is a back door to exactly the silent misattribution capture
    # refuses: `wakil enrich <other id>` would read this file and file its
    # memories under that source instead.
    owner_id = _source_owning_path(config, relative, exclude=source_id)
    if owner_id is not None:
        raise IngestError(
            f"{relative} is already the raw capture of source #{owner_id}. Two sources "
            f"cannot share one file — archive source #{owner_id} first "
            f"(`wakil sources archive {owner_id}`), or relink to a different path."
        )
    with open_session(config) as session:
        workspace_id, _ = _require_workspace_ids(session, config)
        row = session.get(Source, source_id)
        if row is None or row.workspace_id != workspace_id:
            raise IngestError(f"No source with id {source_id} in this workspace.")
        row.raw_text_path = relative
        session.commit()
        return _summarize_source(row)


def archive_source(
    config: WorkspaceConfig,
    source_id: int,
    reason: str | None = None,
    superseded_by: int | None = None,
) -> SourceSummary:
    """Soft-delete a source: keep the row for history, drop it from the
    default listing (#183).

    Not a real delete -- memories, relationships, and ingest_runs reference
    it, and "this attempt was abandoned, the redo is source #N" is worth
    keeping rather than erasing.
    """
    with open_session(config) as session:
        workspace_id, _ = _require_workspace_ids(session, config)
        row = session.get(Source, source_id)
        if row is None or row.workspace_id != workspace_id:
            raise IngestError(f"No source with id {source_id} in this workspace.")
        if superseded_by is not None:
            if superseded_by == source_id:
                raise IngestError("A source cannot supersede itself.")
            replacement = session.get(Source, superseded_by)
            if replacement is None or replacement.workspace_id != workspace_id:
                raise IngestError(f"No source with id {superseded_by} in this workspace.")
        row.archived_at = utcnow()
        row.archive_reason = reason
        row.superseded_by_id = superseded_by
        session.commit()
        return _summarize_source(row)


def unarchive_source(config: WorkspaceConfig, source_id: int) -> SourceSummary:
    """Undo `archive_source`. Archiving is a judgement call and reversible."""
    with open_session(config) as session:
        workspace_id, _ = _require_workspace_ids(session, config)
        row = session.get(Source, source_id)
        if row is None or row.workspace_id != workspace_id:
            raise IngestError(f"No source with id {source_id} in this workspace.")
        row.archived_at = None
        row.archive_reason = None
        row.superseded_by_id = None
        session.commit()
        return _summarize_source(row)


# --------------------------------------------------------------------------
# Step 2: enrichment


def _gather_related_notes(
    session: Session,
    config: WorkspaceConfig,
    source: Source,
    workspace_id: int,
    title: str,
    search_context: str | None,
    text: str,
    context_referenced_paths: list[str],
) -> list[SearchHit]:
    """Related-note candidates for enrichment, in priority order: user
    `@file:` references (guaranteed, ahead of and not subject to
    RELATED_NOTE_LIMIT — the user pointed at them explicitly, so relevance
    ranking shouldn't get a vote), then relevance search, then a direct
    entity-title lookup. Deduped by path via a shared `seen_paths` set so
    the same note is never offered twice under different engines."""
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

    return related_notes


# --------------------------------------------------------------------------
# Enrichment checkpointing (docs/adr/0020): one row per completed DAG phase
# (extraction/resolution/revision/synthesis) so a killed/crashed `wakil
# enrich` run can resume from the last completed phase on re-invocation
# instead of redoing every model call. Only a phase's clean completion is
# ever checkpointed -- a degraded-but-warned outcome from revision/synthesis
# (which never raise) still counts as "complete" and is cached, but a
# ModelContractError from extraction/resolution (which *do* short-circuit
# the DAG) is deliberately never cached, so a transient failure can still be
# retried on the very next invocation rather than becoming permanently
# sticky until --force.


def _checkpoint_content_hash(
    source_content_hash: str | None, context_digest: str | None, model: str
) -> str:
    """Staleness key for a checkpoint row: source content, supplied context,
    and the model in use must all match what produced the checkpoint, or
    it's discarded and the phase is redone from scratch -- never partially
    reused across a changed input (mirrors `_resume_source_branch`'s "if the
    assumption doesn't hold, start fresh" shape, `git_service.py`)."""
    basis = f"{source_content_hash or ''}|{context_digest or ''}|{model}"
    return hashlib.sha256(basis.encode()).hexdigest()


def _load_checkpoint(
    config: WorkspaceConfig, source_id: int, phase: str, content_hash: str
) -> dict | None:
    with open_session(config) as session:
        row = session.scalar(
            select(EnrichmentCheckpoint).where(
                EnrichmentCheckpoint.source_id == source_id,
                EnrichmentCheckpoint.phase == phase,
            )
        )
        if row is None or row.content_hash != content_hash:
            return None
        return json.loads(row.payload_json)


def _save_checkpoint(
    config: WorkspaceConfig,
    source_id: int,
    phase: str,
    content_hash: str,
    model: str,
    payload: dict,
) -> None:
    # Opens its own short-lived session every time rather than sharing one
    # across callers -- Part B runs entity-updates and stub-synthesis
    # concurrently in separate threads, and a Session must never be shared
    # across threads. Safe under the existing WAL + 30s busy_timeout
    # (storage/database.py), which exists for exactly this kind of
    # near-simultaneous short write from independent `wakil` processes/threads.
    with open_session(config) as session:
        workspace_id, _ = _require_workspace_ids(session, config)
        existing = session.scalar(
            select(EnrichmentCheckpoint).where(
                EnrichmentCheckpoint.source_id == source_id,
                EnrichmentCheckpoint.phase == phase,
            )
        )
        payload_json = json.dumps(payload)
        if existing is not None:
            existing.content_hash = content_hash
            existing.payload_json = payload_json
            existing.model = model
        else:
            session.add(
                EnrichmentCheckpoint(
                    workspace_id=workspace_id,
                    source_id=source_id,
                    phase=phase,
                    content_hash=content_hash,
                    payload_json=payload_json,
                    model=model,
                )
            )
        session.commit()


def _clear_checkpoints(config: WorkspaceConfig, source_id: int) -> None:
    """Drop every saved checkpoint for `source_id`. Called up front by
    `--force` (before phase 1, so a forced re-analysis never reuses stale
    phase output) and after a successful `apply_enrichment` (the resume
    window is closed once the source is actually enriched). Deliberately
    NOT called after a declined preview or a failed `validate_proposal` --
    leaving checkpoints in place across exactly that path is the entire
    point of this feature."""
    with open_session(config) as session:
        session.execute(
            delete(EnrichmentCheckpoint).where(EnrichmentCheckpoint.source_id == source_id)
        )
        session.commit()


def _serialize_entity_update(update: EntityUpdate) -> dict:
    return {
        "target_note_path": update.target_note_path,
        "old_content": update.old_content,
        "new_content": update.new_content,
        "confidence": update.confidence,
    }


def _deserialize_entity_update(data: dict) -> EntityUpdate:
    return EntityUpdate(
        target_note_path=data["target_note_path"],
        old_content=data["old_content"],
        new_content=data["new_content"],
        confidence=data.get("confidence"),
    )


def _extraction_checkpoint_payload(proposal: "EnrichmentProposal") -> dict:
    return {
        "title": proposal.title,
        "summary": proposal.summary,
        "key_points": list(proposal.key_points),
        "memories": [
            {
                "memory_type": m.memory_type,
                "content": m.content,
                "confidence": m.confidence,
                "stance": m.stance,
                "event_date": m.event_date.isoformat() if m.event_date else None,
            }
            for m in proposal.memories
        ],
        "relationships": [
            {
                "subject_index": r.subject_index,
                "predicate": r.predicate,
                "object_index": r.object_index,
            }
            for r in proposal.relationships
        ],
        "proposed_note": (
            {
                "path": proposal.proposed_note.path,
                "content": proposal.proposed_note.content,
                "confidence": proposal.proposed_note.confidence,
            }
            if proposal.proposed_note is not None
            else None
        ),
    }


def _apply_extraction_checkpoint(proposal: "EnrichmentProposal", payload: dict) -> None:
    proposal.title = payload["title"]
    proposal.summary = payload["summary"]
    proposal.key_points = list(payload["key_points"])
    proposal.memories = [
        CandidateMemory(
            memory_type=m["memory_type"],
            content=m["content"],
            confidence=m.get("confidence"),
            stance=m.get("stance"),
            event_date=date.fromisoformat(m["event_date"]) if m.get("event_date") else None,
        )
        for m in payload["memories"]
    ]
    proposal.relationships = [
        CandidateRelationship(
            subject_index=r["subject_index"],
            predicate=r["predicate"],
            object_index=r["object_index"],
        )
        for r in payload["relationships"]
    ]
    note = payload["proposed_note"]
    proposal.proposed_note = (
        ProposedFile(path=note["path"], content=note["content"], confidence=note.get("confidence"))
        if note is not None
        else None
    )


def _populate_proposal_from_models(
    config: WorkspaceConfig,
    client: ModelClient,
    source: Source,
    text: str,
    title: str,
    related_notes: list[SearchHit],
    proposal: EnrichmentProposal,
    *,
    on_progress: Callable[[str], None] | None = None,
    checkpoint_hash: str | None = None,
) -> None:
    """Run both DAG model calls (extraction, then entity resolution),
    mutating `proposal` in place with their results -- mirrors this file's
    `_run_extraction`/`_run_entity_resolution` mutate-in-place convention."""
    guides = load_workspace_guides(config)
    related_pairs = [(hit.ref, hit.title) for hit in related_notes]
    source_text = text[:MAX_SOURCE_CHARS]

    # DAG node 1: extraction judgment (the <kind> skill + ExtractionOutput).
    # The raw *capture* path (sources/transcripts/...), not source.origin's
    # pre-capture location — origin may be a binary/external file (a
    # .whisper archive, a URL) the model can't cite as a KB source.
    if on_progress is not None:
        on_progress(f"Extracting content from source {source.id}...")
    cached = (
        _load_checkpoint(config, source.id, "extraction", checkpoint_hash)
        if checkpoint_hash is not None
        else None
    )
    if cached is not None:
        _apply_extraction_checkpoint(proposal, cached)
    else:
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
            CandidateRelationship(
                subject_index=r.subject, predicate=r.predicate, object_index=r.object
            )
            for r in extraction.relationships
        ]
        if extraction.proposed_note is not None:
            proposal.proposed_note = _sanitize_note(
                config,
                ProposedFile(
                    path=extraction.proposed_note.path,
                    content=extraction.proposed_note.markdown,
                ),
                proposal,
            )
            # `_sanitize_note` may rebuild the ProposedFile (path/collision
            # fixes), so the confidence carried on extraction's own output is
            # applied after it settles rather than passed into the
            # constructor above — mirrors `_build_stub_entities` setting
            # `ProposedFile(confidence=...)` for the analogous action=create
            # case (issue #72/#93).
            proposal.proposed_note.confidence = _clamp01(
                extraction.proposed_note.frontmatter_confidence
            )
        if checkpoint_hash is not None:
            _save_checkpoint(
                config,
                source.id,
                "extraction",
                checkpoint_hash,
                client.model,
                _extraction_checkpoint_payload(proposal),
            )

    # DAG node 2: entity resolution — always invoked, never optional.
    _run_entity_resolution(
        config,
        client,
        source_text,
        related_pairs,
        proposal,
        guides,
        on_progress=on_progress,
        checkpoint_hash=checkpoint_hash,
    )
    _warn_if_nothing_produced(source.id, proposal)


def prepare_enrichment(
    config: WorkspaceConfig,
    source_id: int,
    client: ModelClient,
    context: str | None = None,
    context_digest: str | None = None,
    context_referenced_paths: list[str] | None = None,
    force: bool = False,
    *,
    on_progress: Callable[[str], None] | None = None,
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
        # Fallback for a Timeline heading with no real date of its own
        # (issue #77) — retrieved_at is set at capture time for every
        # source; created_at covers the rare row without it.
        captured_at = source.retrieved_at or source.created_at
        # An absolute instant, so `.date()` would give the UTC day -- and this
        # one is written into an append-only Timeline heading, so a wrong date
        # here is permanent in the user's KB.
        source_captured_date = workspace_date(config, captured_at) if captured_at else None
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

        related_notes = _gather_related_notes(
            session,
            config,
            source,
            workspace_id,
            title,
            search_context,
            text,
            context_referenced_paths,
        )

    if force:
        # A forced re-analysis must never reuse phase output from a
        # previous run -- start fully fresh.
        _clear_checkpoints(config, source_id)
    checkpoint_hash = _checkpoint_content_hash(source.content_hash, context_digest, client.model)

    proposal = EnrichmentProposal(
        source_id=source_id,
        title=title,
        context=context,
        related_notes=related_notes,
        source_captured_date=source_captured_date,
    )
    proposal.model = client.model
    _populate_proposal_from_models(
        config,
        client,
        source,
        text,
        title,
        related_notes,
        proposal,
        on_progress=on_progress,
        checkpoint_hash=checkpoint_hash,
    )
    return proposal


def _warn_if_nothing_produced(source_id: int, proposal: EnrichmentProposal) -> None:
    """Whole-proposal visibility check (issue #44): every skip along the way
    (a notability judgment in entity-resolve/SKILL.md, a below-relevance
    update, a has_update=False revision, a failed model call) already
    degrades visibly on its own, but none of them know whether *every other*
    path for this source also came up empty. If proposed_note, stub_entities,
    and entity_updates are all empty here, the source is about to be applied
    (or previewed) as a complete no-op — say so once, naming the source,
    rather than leaving that silent."""
    nothing_produced = (
        proposal.proposed_note is None
        and not proposal.stub_entities
        and not proposal.entity_updates
    )
    if nothing_produced:
        proposal.warnings.append(
            f"Source {source_id} ('{proposal.title}'): enrichment produced no new page, "
            "stub, or update for any entity — nothing will be written for this source."
        )


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
    session,
    workspace_id: int,
    text: str,
    schemas: dict,
    *,
    extra_terms: set[str] | None = None,
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
    candidates |= extra_terms or set()
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
    page_shapes: dict[str, str] = {}
    for schema in schemas.values():
        # Every non-disabled loaded schema is validated to require page_shape.
        assert schema.page_shape is not None
        page_shapes[schema.page_shape] = resolve_page_shape_template(
            schema.page_shape, config.root_path
        )[0]
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
    *,
    on_progress: Callable[[str], None] | None = None,
    checkpoint_hash: str | None = None,
) -> None:
    """Second model call plus stub-page construction; degrades visibly."""
    if on_progress is not None:
        on_progress("Resolving entities...")
    cached = (
        _load_checkpoint(config, proposal.source_id, "resolution", checkpoint_hash)
        if checkpoint_hash is not None
        else None
    )
    if cached is not None:
        proposal.entity_resolutions = [
            EntityResolution.model_validate(r) for r in cached["entity_resolutions"]
        ]
    else:
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
            # Deliberately never checkpointed: a transient failure here
            # should still be retriable on the very next invocation rather
            # than becoming permanently sticky until --force.
            proposal.warnings.append(f"Entity resolution failed; no entity pages proposed: {exc}")
            return
        proposal.entity_resolutions = list(resolution.entities)
        if checkpoint_hash is not None:
            _save_checkpoint(
                config,
                proposal.source_id,
                "resolution",
                checkpoint_hash,
                client.model,
                {"entity_resolutions": [r.model_dump(mode="json") for r in resolution.entities]},
            )
    # _build_stub_entities is pure code re-run fresh every time (live or
    # resumed) from whichever entity_resolutions ended up on the proposal --
    # never itself persisted, so it can't drift from what a live run would
    # produce for the same resolutions.
    proposal.stub_entities = _build_stub_entities(config, proposal)
    # Entity updates (DAG node 3) and stub-content synthesis (DAG node 4)
    # touch disjoint entity sets, so they run concurrently -- but suppression
    # (below) prunes proposal.stub_entities using node 3's own output, so
    # node 4 is handed a pre-suppression snapshot rather than reading
    # proposal.stub_entities live (see _synthesize_stub_content's docstring
    # for why that's still correct for stubs that survive suppression).
    stub_snapshot = list(proposal.stub_entities)
    with ThreadPoolExecutor(max_workers=2) as executor:
        updates_future = executor.submit(
            _run_entity_updates,
            config,
            client,
            text,
            proposal,
            on_progress=on_progress,
            checkpoint_hash=checkpoint_hash,
        )
        synthesis_future = executor.submit(
            _synthesize_stub_content,
            config,
            client,
            text,
            proposal,
            stub_snapshot,
            on_progress=on_progress,
            checkpoint_hash=checkpoint_hash,
        )
        updates_future.result()
        synthesis_future.result()
    # Entity updates must run before link reconciliation: it can further
    # prune stub_entities (see _suppress_stubs_matching_updates), and
    # reconciliation needs the final stub set to correct links against.
    _suppress_stubs_matching_updates(proposal)
    _suppress_dated_record_stubs_matching_updates(config, proposal)
    _suppress_proposed_note_matching_updates(proposal)
    _reconcile_entity_links(config, proposal)


def _file_identity(proposed: ProposedFile) -> tuple[str | None, str | None]:
    """What entity a proposed file claims to be about: `(type, name-slug)`
    from its own frontmatter. This is a page's identity; its path is not."""
    try:
        metadata = frontmatter_lib.loads(proposed.content).metadata
    except Exception:
        return (None, None)
    if not isinstance(metadata, dict):
        return (None, None)
    subject = metadata.get("name") or metadata.get("title")
    slug = slugify(subject) if isinstance(subject, str) and subject.strip() else None
    return (_entity_type_of(metadata), slug)


def _duplicate_of_proposed_note(
    proposal: "EnrichmentProposal", candidate: ProposedFile
) -> str | None:
    """The proposed note's path when `candidate` describes the same entity.

    Path/slug comparison isn't enough: the whole point of #186 is that a
    filename correction can leave two paths that differ while the pages they
    name are the same entity, with byte-identical `type:` and `name:`."""
    note = proposal.proposed_note
    if note is None:
        return None
    identity = _file_identity(candidate)
    if identity == (None, None) or identity[1] is None:
        return None
    return note.path if _file_identity(note) == identity else None


def _proposed_note_subject_slug(proposed_note: ProposedFile | None) -> str | None:
    """The slugified name/title a proposed_note's own frontmatter claims —
    its identity, for comparison against entity-resolution's create
    proposals. Returns None if there's no proposed_note or its frontmatter
    doesn't parse/carry a usable label."""
    if proposed_note is None:
        return None
    try:
        metadata = frontmatter_lib.loads(proposed_note.content).metadata
    except Exception:
        return None
    if not isinstance(metadata, dict):
        return None
    subject = metadata.get("name") or metadata.get("title")
    if not isinstance(subject, str) or not subject.strip():
        return None
    return slugify(subject)


def _populate_type_frontmatter(
    schema: EntitySchema,
    entity_type: str,
    proposed_frontmatter: dict | None,
    fallback_label: str,
    today: str,
) -> dict:
    """Build frontmatter for a note being written under a schema for the first time.

    Used both when creating a new stub in `_build_stub_entities` and when
    changing an existing proposal's type in `_correct_proposed_note_type`
    (issue #92).

    The returned frontmatter:

    - sets `type` to `entity_type`;
    - uses `title` as the label field for document schemas and `name` for
      all other schemas;
    - uses the proposed label when it is present and truthy, otherwise
      uses `fallback_label`;
    - copies the remaining fields from `proposed_frontmatter`; and
    - sets `created` and `updated` to `today` when those fields are defined
      by the schema but do not already have a value.

    Any `type` value in `proposed_frontmatter` is ignored so that it cannot
    override `entity_type`.

    For a new stub, `fallback_label` is typically `resolution.name`. During
    type correction, it is typically the existing note's `name` or `title`.
    """
    proposed = dict(proposed_frontmatter or {})
    proposed.pop("type", None)
    label_field = "title" if schema.category == "document" else "name"
    metadata: dict = {
        "type": entity_type,
        label_field: proposed.pop(label_field, None) or fallback_label,
    }
    metadata.update(proposed)
    for date_field in ("created", "updated"):
        if date_field in schema.fields and not metadata.get(date_field):
            metadata[date_field] = today
    return metadata


def _correct_proposed_note_type(
    proposed_note: ProposedFile,
    resolution: EntityResolution,
    schema: EntitySchema,
    proposal: "EnrichmentProposal",
    today: str,
    config: WorkspaceConfig,
) -> ProposedFile:
    """When a create-resolution's subject matches proposed_note's own
    subject (see `_proposed_note_subject_slug`) but entity-resolution
    disagrees with proposed_note's own `type:`, entity-resolution's decision
    wins: it runs after extraction and gets to see sibling precedents and
    the full entity catalog that extraction never had (issue #73).
    Previously only the redundant stub was suppressed in this case, leaving
    extraction's earlier, less-informed type on disk uncorrected.

    A bare `type:` relabel isn't enough (issue #92): the old and new types
    can have entirely different required fields (e.g. an `index`'s
    title/tags/created vs. a `project`'s name/created/updated), so the
    frontmatter is rebuilt via the same `_populate_type_frontmatter` helper
    `_build_stub_entities` uses for a fresh stub -- falling back to the
    note's own existing `name`/`title` value for the label field (rather
    than `resolution.name`, since the note's own subject wording is already
    reviewed content) and backfilling `created`/`updated` when the new
    schema requires them. Only the frontmatter and the file's directory
    change; the synthesized markdown body (and its own filename slug,
    already normalized by `_sanitize_note`) is left untouched -- mirrors
    `_reslug_proposed_note`'s "record the correction, never apply it
    invisibly" pattern. Returns `proposed_note` unchanged when the types
    already agree (including when the note's own frontmatter doesn't parse,
    or carries no `type:` at all -- validate_proposal's own type check
    catches that separately).
    """
    try:
        post = frontmatter_lib.loads(proposed_note.content)
    except Exception:
        return proposed_note
    metadata = post.metadata if isinstance(post.metadata, dict) else {}
    old_type = metadata.get("type")
    if old_type == resolution.entity_type:
        return proposed_note

    existing_label = str(metadata.get("name") or metadata.get("title") or resolution.name)
    new_metadata = _populate_type_frontmatter(
        schema, resolution.entity_type, resolution.proposed_frontmatter, existing_label, today
    )
    frontmatter_yaml = yaml.safe_dump(new_metadata, sort_keys=False, allow_unicode=True)
    new_content = f"---\n{frontmatter_yaml}---\n\n{post.content}"
    # The fourth mover of a proposed note's path, and the one outside
    # `_sanitize_note`. It needs both of that function's guarantees or this
    # path keeps the two defects the others just closed: an unchecked
    # destination discards the whole run in `apply_enrichment`, and an
    # un-retargeted self-link dangles.
    root = config.root_path.resolve()
    routed = Path(f"{schema.directory}/{Path(proposed_note.path).name}")
    new_path = str(_free_target(root, routed, proposal))

    proposal.warnings.append(
        f"Corrected the proposed note's type from '{old_type}' to "
        f"'{resolution.entity_type}' (moving it from {proposed_note.path} to {new_path}) "
        "to match entity-resolution's decision for the same subject"
    )
    return _retarget_self_links(
        ProposedFile(path=proposed_note.path, content=new_content), new_path
    )


def _is_source_self_mirror(resolution: EntityResolution, proposal: EnrichmentProposal) -> bool:
    """Would this `entity_type: source` create just mirror the very source
    being enriched (see issue #58)?

    The source's own raw capture already exists on disk (that's what
    prepare_enrichment was called on); a create-resolution proposing
    `entity_type: source` for the same subject is inherently redundant, not
    a legitimate new page. A source citing some *other*, distinct source is
    rare and stays out of scope — the ability to distinguish the two cases
    is limited to what's already in the proposal, so the signal used is
    slug overlap between the resolution's name and the source's own title:
    exact matches ("Deep Work" == "Deep Work"), decorated variants ("Deep
    Work Highlights" over "Deep Work"), and cases with substantial shared
    wording all count; a name sharing no real wording with the source's own
    title (a distinctly-named book or article it merely cites) does not.
    """
    if resolution.entity_type != "source":
        return False
    name_slug = slugify(resolution.name)
    title_slug = slugify(proposal.title)
    if not name_slug or not title_slug or name_slug == "untitled" or title_slug == "untitled":
        return False
    if name_slug == title_slug or name_slug.startswith(title_slug) or title_slug.startswith(
        name_slug
    ):
        return True
    name_words = {w for w in name_slug.split("-") if len(w) > 2}
    title_words = {w for w in title_slug.split("-") if len(w) > 2}
    if not name_words or not title_words:
        return False
    overlap = name_words & title_words
    return len(overlap) / min(len(name_words), len(title_words)) >= 0.5


def _suppress_duplicate_of_proposed_note(
    resolution: EntityResolution,
    schema: EntitySchema,
    proposal: EnrichmentProposal,
    taken: set[str],
    today: str,
    proposed_note_slug: str | None,
    config: WorkspaceConfig,
) -> bool:
    """Suppress `resolution` when its subject duplicates proposal.proposed_note
    (see issue #36). Returns True when suppressed, in which case the caller
    should treat the resolution as handled (kept in entity_resolutions,
    no stub built).
    """
    resolution_slug = slugify(resolution.name)
    if proposed_note_slug is None or resolution_slug != proposed_note_slug:
        return False
    if proposal.proposed_note is not None:
        old_path = proposal.proposed_note.path
        proposal.proposed_note = _correct_proposed_note_type(
            proposal.proposed_note, resolution, schema, proposal, today, config
        )
        if proposal.proposed_note.path != old_path:
            taken.discard(old_path)
            taken.add(proposal.proposed_note.path)
    # proposed_note_slug is not None implies proposal.proposed_note was set
    # when computed by the caller, and it's only ever reassigned (never
    # cleared) above.
    assert proposal.proposed_note is not None
    proposal.warnings.append(
        f"{resolution.name}: already represented by the proposed note "
        f"({proposal.proposed_note.path}) — not creating a duplicate page"
    )
    return True


def _build_stub_or_skip(
    resolution: EntityResolution,
    schema: EntitySchema,
    proposal: EnrichmentProposal,
    taken: set[str],
    today: str,
    config: WorkspaceConfig,
) -> ProposedFile | None:
    """Build the stub page for `resolution`, or return None (recording a
    warning) when its path already exists on disk or is already taken by
    another proposed file in this same proposal.
    """
    path = f"{schema.directory}/{slugify(resolution.name)}.md"
    if (config.root_path / path).exists():
        proposal.warnings.append(
            f"{resolution.name}: {path} already exists — not creating a duplicate page"
        )
        return None
    if path in taken:
        return None
    metadata = _populate_type_frontmatter(
        schema, resolution.entity_type, resolution.proposed_frontmatter, resolution.name, today
    )
    stub = ProposedFile(
        path=path,
        content=_stub_content(metadata, resolution.name),
        confidence=resolution.proposed_frontmatter_confidence,
    )
    # Identity is (type, name) as actually written, not the path and not
    # `resolution.name` as passed in. `_reslug_proposed_note` can rewrite the
    # proposed note's *filename* into a near-neighbour of this stub's, at
    # which point two files describing one entity -- with byte-identical
    # `type:` and `name:` frontmatter -- both pass the path-uniqueness check
    # and both get written (#186). Comparing the built content catches that
    # however the two names were derived upstream.
    duplicate = _duplicate_of_proposed_note(proposal, stub)
    if duplicate is not None:
        proposal.warnings.append(
            f"{resolution.name}: skipped a stub page that duplicates the proposed note "
            f"{duplicate} — both are `{_file_identity(stub)[0]}` named "
            f"'{_file_identity(stub)[1]}'"
        )
        return None
    taken.add(path)
    return stub


def _build_stub_entities(
    config: WorkspaceConfig, proposal: EnrichmentProposal
) -> list[ProposedFile]:
    """One stub page per notable new entity (action=create), schema-routed.

    Unknown entity types (no schema at all) build no stub here and are left
    in proposal.entity_resolutions — validate_proposal() reports those as a
    hard stop, which is correct: the type is genuinely unrecognized.

    Types that DO have a schema but no canonical directory (e.g. `index`,
    a MOC/nav page with nowhere to be filed) are a different situation:
    the source is real and recognized, it just needs manual placement.
    A warning is recorded so the skip is visible in the enrich preview, and
    the resolution is dropped from proposal.entity_resolutions so
    validate_proposal's create-scanning loop never sees it — otherwise its
    hard stop would abort the entire apply (including this proposal's
    unrelated proposed_note and other stubs/updates) over a case that's
    already been surfaced as a warning, not an error.

    Extraction (proposed_note) and entity resolution are independent model
    calls, and both can independently decide to represent the *same*
    real-world subject — usually under a different entity type, so the
    proposed path never collides with proposed_note.path even though it's a
    duplicate in substance. A create-resolution whose slugified name matches
    proposed_note's own name/title is suppressed here rather than written as
    a second, always-empty page for the same subject (see issue #36).

    A create-resolution of `entity_type: source` that just mirrors the
    source currently being enriched is suppressed the same way (issue #58):
    the raw source is already captured on disk, so re-proposing it as its
    own entity is a structural no-op that satisfies "don't skip the
    source's own subject" (issue #44) without ever creating the actual
    domain entity that guidance was meant to produce.
    """
    schemas = load_entity_schemas(config.root_path)
    today = workspace_today(config)
    stubs: list[ProposedFile] = []
    taken = {proposal.proposed_note.path} if proposal.proposed_note else set()
    kept_resolutions: list[EntityResolution] = []
    proposed_note_slug = _proposed_note_subject_slug(proposal.proposed_note)

    for resolution in proposal.entity_resolutions:
        if resolution.action != "create":
            kept_resolutions.append(resolution)
            continue
        schema = schemas.get(resolution.entity_type)
        if schema is None:
            # Genuinely unknown type: keep it in entity_resolutions so
            # validate_proposal's hard stop still fires for it.
            proposal.warnings.append(
                f"{resolution.name}: type '{resolution.entity_type}' has no canonical "
                "directory to route into — needs manual placement"
            )
            kept_resolutions.append(resolution)
            continue
        if schema.directory is None:
            # Known type, no canonical directory: warn and drop, so
            # validate_proposal doesn't hard-stop the whole apply over it.
            proposal.warnings.append(
                f"{resolution.name}: type '{resolution.entity_type}' has no canonical "
                "directory to route into — needs manual placement"
            )
            continue
        if _suppress_duplicate_of_proposed_note(
            resolution, schema, proposal, taken, today, proposed_note_slug, config
        ):
            kept_resolutions.append(resolution)
            continue
        if _is_source_self_mirror(resolution, proposal):
            proposal.warnings.append(
                f"{resolution.name}: entity_type 'source' would just mirror the source "
                "already being enriched — not creating a redundant self-page; the actual "
                "domain entity for this subject was likely never decided"
            )
            kept_resolutions.append(resolution)
            continue
        stub = _build_stub_or_skip(resolution, schema, proposal, taken, today, config)
        kept_resolutions.append(resolution)
        if stub is not None:
            stubs.append(stub)

    proposal.entity_resolutions = kept_resolutions
    return stubs


def _suppress_stubs_matching_updates(proposal: EnrichmentProposal) -> None:
    """Drop a create-resolution's stub when its subject already has a home
    via an entity update (DAG node 3) computed in this same proposal.

    Extraction/entity-resolution proposing a create is independent of
    entity-resolution's own update resolutions — a source can correctly
    merge into an existing long-lived entity's Timeline (entity_updates) and
    *also* independently propose a "create" for the same subject under a
    different (often builtin) type, e.g. journal/meeting. Matching against
    the applied entity_updates (rather than every action=update resolution)
    keeps this conservative: an update that entity-resolution proposed but
    that turned out to warrant no real content change is not treated as
    "already has a home."
    """
    if not proposal.entity_updates or not proposal.stub_entities:
        return

    updated_paths = {update.target_note_path for update in proposal.entity_updates}
    # Subject identity for each applied update: the update target's own file
    # stem, plus the name entity-resolution used for the matching resolution
    # (they can differ, e.g. a display name vs. an already-slugified path).
    updated_slugs = {slugify(Path(path).stem) for path in updated_paths}
    for resolution in proposal.entity_resolutions:
        if resolution.action == "update" and resolution.target_note_path in updated_paths:
            updated_slugs.add(slugify(resolution.name))

    kept: list[ProposedFile] = []
    for stub in proposal.stub_entities:
        if slugify(Path(stub.path).stem) in updated_slugs:
            proposal.warnings.append(
                f"{stub.path}: subject already updated via an existing entity in this "
                "same proposal — not creating a duplicate page"
            )
            continue
        kept.append(stub)
    proposal.stub_entities = kept


# Entity types whose whole reason to exist is recording "what happened via
# this source" for one dated occurrence (docs/entity-metadata.md) — never a
# type a vault would want *in addition to* an update this same source
# already made elsewhere in this same proposal. Deliberately narrower than
# "every single-occurrence-shaped type" (issue #60): a personal `reflection`
# entity, say, could still be a genuinely distinct record even when it
# shares a source with a project's factual Timeline update, so this list is
# hand-picked rather than schema-driven.
_REDUNDANT_DATED_RECORD_TYPES = frozenset({"journal", "meeting"})


def _suppress_dated_record_stubs_matching_updates(
    config: WorkspaceConfig, proposal: EnrichmentProposal
) -> None:
    """Drop a journal/meeting create-resolution's stub when this same
    source's content already merged into an existing accumulating entity via
    entity_updates (DAG node 3) in this same proposal.

    Complements _suppress_stubs_matching_updates, which only catches a
    create whose own subject slug matches the update target's slug (e.g.
    extraction and entity-resolution both proposing "Elektrum" under
    different types). It structurally cannot catch a *dated* journal/meeting
    record, whose name/slug is the date/topic — e.g. "2014-05-17 Elektrum
    VPC Subnet Layout Finalized" (slug
    elektrum-work-vpc-subnet-layout-finalized) — never the project it's
    about ("elektrum"), so the two can never slug-match even when both
    represent the same source landing twice (issue #60). Matching by type
    rather than subject keeps this the narrow case it's meant to be: journal
    and meeting exist specifically to log "what happened via this source,"
    so an update that already did that in this same proposal makes them
    redundant — this does NOT extend to every single-occurrence type (see
    _REDUNDANT_DATED_RECORD_TYPES).
    """
    if not proposal.entity_updates or not proposal.stub_entities:
        return

    schemas = load_entity_schemas(config.root_path)
    stub_types: dict[str, str] = {}
    for resolution in proposal.entity_resolutions:
        if resolution.action != "create":
            continue
        if resolution.entity_type not in _REDUNDANT_DATED_RECORD_TYPES:
            continue
        schema = schemas.get(resolution.entity_type)
        if schema is None or schema.directory is None:
            continue
        path = f"{schema.directory}/{slugify(resolution.name)}.md"
        stub_types[path] = resolution.entity_type

    if not stub_types:
        return

    kept: list[ProposedFile] = []
    for stub in proposal.stub_entities:
        entity_type = stub_types.get(stub.path)
        if entity_type is not None:
            proposal.warnings.append(
                f"{stub.path}: this source's content already merged into an existing "
                "entity via an update in this same proposal — not creating a separate "
                f"{entity_type} record for the same source"
            )
            continue
        kept.append(stub)
    proposal.stub_entities = kept


def _suppress_proposed_note_matching_updates(proposal: EnrichmentProposal) -> None:
    """Null out proposal.proposed_note when it's redundant with an entity
    update (DAG node 3) applied in this same proposal (issue #68).

    _suppress_stubs_matching_updates and
    _suppress_dated_record_stubs_matching_updates both only ever prune
    proposal.stub_entities — an entity-resolution create. Neither touches
    proposal.proposed_note, which is set by extraction (_run_extraction),
    an independent model call that runs *before* entity resolution and so
    can never see entity_updates at all. The same duplication both of those
    functions guard against for a stub can happen to proposed_note instead:
    a dated journal-style source can correctly update an existing
    accumulating entity's Timeline via entity_updates and *also*
    independently produce its own proposed_note for the same source.

    Two redundancy signals, mirroring the two suppression functions above:

    1. Subject-slug match (mirrors _suppress_stubs_matching_updates):
       proposed_note's own frontmatter name/title slug
       (_proposed_note_subject_slug) matches an applied entity_updates
       target's slug — same subject, just extraction's independent
       representation of it.
    2. Dated-record type (mirrors _suppress_dated_record_stubs_matching_updates):
       proposed_note's frontmatter `type` is journal or meeting
       (_REDUNDANT_DATED_RECORD_TYPES) and this same proposal already
       produced a real entity_update — a journal/meeting note's whole
       purpose is "record what this source said," which an update that
       already recorded it makes redundant, even when proposed_note's own
       subject (a date/topic) never slug-matches the update target.

    Unlike the stub-suppression functions there's only ever one candidate
    (proposal.proposed_note itself), so this is a null-or-keep decision,
    not a filtered list.
    """
    if not proposal.entity_updates or proposal.proposed_note is None:
        return

    updated_paths = {update.target_note_path for update in proposal.entity_updates}
    updated_slugs = {slugify(Path(path).stem) for path in updated_paths}
    for resolution in proposal.entity_resolutions:
        if resolution.action == "update" and resolution.target_note_path in updated_paths:
            updated_slugs.add(slugify(resolution.name))

    proposed_note_slug = _proposed_note_subject_slug(proposal.proposed_note)
    if proposed_note_slug is not None and proposed_note_slug in updated_slugs:
        proposal.warnings.append(
            f"{proposal.proposed_note.path}: subject already updated via an existing "
            "entity in this same proposal — not creating a duplicate page"
        )
        proposal.proposed_note = None
        return

    try:
        metadata = frontmatter_lib.loads(proposal.proposed_note.content).metadata
    except Exception:
        metadata = {}
    entity_type = metadata.get("type") if isinstance(metadata, dict) else None
    if entity_type in _REDUNDANT_DATED_RECORD_TYPES:
        proposal.warnings.append(
            f"{proposal.proposed_note.path}: this source's content already merged into "
            "an existing entity via an update in this same proposal — not creating a "
            f"separate {entity_type} record for the same source"
        )
        proposal.proposed_note = None


def _synthesize_stub_content(
    config: WorkspaceConfig,
    client: ModelClient,
    text: str,
    proposal: EnrichmentProposal,
    stubs: list[ProposedFile],
    *,
    on_progress: Callable[[str], None] | None = None,
    checkpoint_hash: str | None = None,
) -> None:
    """Fourth model call: populate each surviving create-stub's Compiled
    Truth / Timeline from this source, instead of leaving `_stub_content`'s
    hardcoded placeholder on disk forever (issue #70) — every fresh-create
    note used to reach disk empty regardless of how much the source actually
    said about it.

    `stubs` is a pre-suppression snapshot of `proposal.stub_entities`
    (passed explicitly, not read from `proposal` here) so this can run
    concurrently with `_run_entity_updates` in `_run_entity_resolution`,
    ahead of the suppression passes that prune `proposal.stub_entities`
    using that call's output. The `ProposedFile` objects in the snapshot are
    the same instances still referenced by `proposal.stub_entities`, so a
    `stub.content` mutation here still lands correctly for any stub that
    survives suppression; content synthesized for a stub suppression later
    discards is simply thrown away with it.

    Deliberately reuses `_run_entity_updates`'s own machinery rather than
    inventing a parallel one: the same `EntityRevision` contract, the same
    `note-revision` skill and `build_revision_prompt`, and the same
    `_merge_entity_note` surgical merge. The trick is that a from-scratch
    create and a revision of a still-unpopulated stub are structurally the
    same problem — `_is_unpopulated_stub` (issue #45) already treats
    `_stub_content`'s exact placeholder text as "nothing has ever been
    synthesized here yet," so passing that same about-to-be-written
    placeholder in as `old_content` and asking the same "does this source
    warrant an update" question the update path asks is a faithful call, not
    a repurposed one: the note's actual current content on disk *is* that
    placeholder. `has_update=False` (the source doesn't actually support any
    synthesizable content for this entity) leaves the stub exactly as
    `_build_stub_entities` wrote it — a minimal, honest placeholder, never a
    fabrication.

    Degrades visibly: a failed call leaves every stub with its plain
    placeholder content plus a warning, never a crash — mirrors
    `_run_entity_resolution`'s own `ModelContractError` handling.
    """
    if not stubs:
        return

    cached = (
        _load_checkpoint(config, proposal.source_id, "synthesis", checkpoint_hash)
        if checkpoint_hash is not None
        else None
    )
    if cached is not None:
        overrides = cached["content_by_path"]
        for stub in stubs:
            if stub.path in overrides:
                stub.content = overrides[stub.path]
        return

    if on_progress is not None:
        count = len(stubs)
        entity_word = "entity" if count == 1 else "entities"
        on_progress(f"Synthesizing content for {count} new {entity_word}...")
    targets = [(stub.path, stub.content) for stub in stubs]
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
        count = len(targets)
        entity_word = "entity" if count == 1 else "entities"
        names = ", ".join(path for path, _ in targets)
        proposal.warnings.append(
            f"Initial content synthesis failed for {count} new {entity_word} ({names}); "
            f"created as empty placeholder stub{'s' if count != 1 else ''} instead: {exc}"
        )
        return

    today = workspace_today(config)
    by_path = {stub.path: stub for stub in stubs}
    for revision in result.revisions:
        stub = by_path.get(revision.target_note_path)
        if stub is None or not revision.has_update:
            # has_update=False: the source doesn't support real content for
            # this entity -- leave _build_stub_entities' placeholder as-is.
            continue
        merged = _merge_entity_note(stub.content, revision, today)
        if merged is not None:
            stub.content = merged

    if checkpoint_hash is not None:
        _save_checkpoint(
            config,
            proposal.source_id,
            "synthesis",
            checkpoint_hash,
            client.model,
            {"content_by_path": {stub.path: stub.content for stub in stubs}},
        )


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


# Matches a Timeline entry's own heading line, e.g. "### 2026-07-16 —
# what happened". Used only to detect whether it carries a real date —
# see `_dated_timeline_entry` below.
_ENTRY_HEADING_RE = re.compile(r"^(#{2,4})\s+(.*)$")
_ISO_DATE_IN_HEADING_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _dated_timeline_entry(entry: str, fallback_date: str | None) -> str:
    """Replace `entry`'s heading with `fallback_date` when it doesn't carry
    a real, parseable date of its own.

    Timeline entries are append-only (never rewritten once written), so a
    placeholder heading the model invents when the source itself has no
    date — "(date not recorded)", "Undated -- source: clipping" — would
    otherwise sit there permanently (issue #77). Falls back to the source's
    own captured/retrieved date instead of accepting that text verbatim.
    Any description text after a separator (" — ", " -- ", " - ") is kept;
    only the placeholder date token itself is replaced. A no-op when
    `fallback_date` is unavailable or the heading already has a real date.
    """
    if not fallback_date:
        return entry
    lines = entry.splitlines()
    if not lines:
        return entry
    match = _ENTRY_HEADING_RE.match(lines[0])
    if match is None or _ISO_DATE_IN_HEADING_RE.search(match.group(2)):
        return entry
    marker, rest = match.group(1), match.group(2).strip()
    for sep in (" — ", " -- ", " - "):
        if sep in rest:
            description = rest.partition(sep)[2].strip()
            if description:
                lines[0] = f"{marker} {fallback_date} — {description}"
                return "\n".join(lines)
    lines[0] = f"{marker} {fallback_date}"
    return "\n".join(lines)


def _insert_timeline_entry(
    timeline_section: str, entry: str, fallback_date: str | None = None
) -> str:
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
    entry = _dated_timeline_entry(entry, fallback_date)
    return f"{heading_line}\n\n{entry}\n\n{rest}" if rest else f"{heading_line}\n\n{entry}\n"


_TRAILING_HR_RE = re.compile(r"\n*-{3,}\s*$")

# Embed form per note-conformance/SKILL.md: `![[target|alias]]` — target
# first, alias second, same order as a plain wikilink but with the leading
# `!`. Deliberately distinct from `_WIKILINK_RE` (wakil.knowledge.wikilinks):
# reusing that regex here would also rewrite plain `[[wikilink]]` references,
# which aren't attachments and must never be re-pathed by this logic.
_EMBED_RE = re.compile(r"!\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


def _entity_attachment_dir(target_note_path: str) -> str:
    """Vault-root-absolute sibling attachment folder for an entity note,
    named exactly after the note's own filename — RESOLVER.md rule 5's
    convention (issue #76): `projects/pencil-box.md` -> `projects/pencil-box`.
    """
    note_path = PurePosixPath(target_note_path)
    return str(note_path.parent / note_path.stem)


def _normalize_new_embed_paths(old_content: str, text: str, target_note_path: str) -> str:
    """Rewrite embed targets this revision newly introduces to a
    vault-root-absolute path under the destination entity's own sibling
    attachment folder (issue #76), instead of leaving whatever bare-filename
    or source-relative path the model wrote verbatim — which never resolves
    from the vault root and leaves the link permanently dangling.

    Conservative by design:
    - An embed whose exact `![[...]]` text already appears anywhere in
      `old_content` is left untouched — this only normalizes references the
      merge is newly adding, never something already on the page (which may
      have been placed there deliberately, by a human edit or a prior pass).
    - An embed target that's an external URL (`http://`/`https://`) is left
      alone; the sibling-attachment convention only applies to local files.
    - This never copies or touches any file on disk. wakil has no existing
      mechanism, on any path (create or update), for locating or copying an
      attachment file into the vault — the capture pipeline stores only
      extracted text, never a source's original binary attachments, and raw
      captures share a common bucket directory rather than an isolated
      per-source one. Building that is a materially larger, separate
      problem (issue #76's PR notes it as follow-up, not fixed here); this
      function only makes the resulting reference point at the right place
      once a file *is* dropped there by hand.
    """
    attachment_dir = _entity_attachment_dir(target_note_path)

    def _replace(match: re.Match) -> str:
        whole = match.group(0)
        if whole in old_content:
            return whole  # pre-existing embed, carried forward verbatim
        target = match.group(1).strip()
        alias = match.group(2)
        if target.lower().startswith(("http://", "https://")):
            return whole
        basename = target.rsplit("/", 1)[-1]
        expected = f"{attachment_dir}/{basename}"
        if target == expected:
            return whole
        return f"![[{expected}|{alias}]]" if alias is not None else f"![[{expected}]]"

    return _EMBED_RE.sub(_replace, text)


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


def _merge_entity_note(
    old_content: str,
    revision: EntityRevision,
    today: str,
    fallback_date: str | None = None,
) -> str | None:
    """Deterministic surgical merge — never a full-file regeneration (the
    "clobbering bug" note-revision's own skill warns against): existing
    frontmatter with only the delta keys changed, the H1 line preserved
    verbatim (slug consistency), everything between the H1 and the Timeline
    heading replaced with the re-synthesized compiled_truth, and one new
    entry prepended inside the Timeline section. Returns None if the note
    doesn't have the expected H1 + '## Timeline / Log' shape — the caller
    surfaces that as a warning rather than guessing at a different one.

    `fallback_date` (the source's own captured/retrieved date) stands in
    for `revision.timeline_entry`'s heading when that heading has no real
    date of its own — see `_dated_timeline_entry`. The entity-compile pilot
    callers never set `timeline_entry`, so they can leave this at its
    default.
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
    # Assign a real date object, not the isoformat string `today` actually
    # is: yaml.safe_dump quotes a plain string that merely looks like a
    # date (to disambiguate it from a date scalar), but dumps an actual
    # date object unquoted. `created` survives as a date object from the
    # frontmatter round-trip (PyYAML's implicit resolver parses unquoted
    # `created: 2026-01-15` into a date on load), so without this cast
    # `updated:` would be the only quoted date field in the frontmatter.
    metadata["updated"] = date.fromisoformat(today)

    # An empty/absent compiled_truth means "no change to the top section",
    # never "delete the top section" — has_update=True can legitimately mean
    # only the Timeline changed. Wiping existing State prose whenever the
    # model didn't re-send it is the exact clobbering bug this merge exists
    # to prevent (docs/TROUBLESHOOTING.md).
    compiled_truth = (revision.compiled_truth or "").strip() or old_top
    target_note_path = revision.target_note_path
    # Issue #76: any embed this revision newly introduces gets its target
    # normalized to the destination entity's own sibling attachment folder,
    # vault-root-absolute, rather than whatever bare/relative path the
    # model wrote. Pre-existing embeds (already in old_content) are left
    # untouched by this call — see _normalize_new_embed_paths.
    compiled_truth = _normalize_new_embed_paths(old_content, compiled_truth, target_note_path)
    timeline_entry = _normalize_new_embed_paths(
        old_content, revision.timeline_entry or "", target_note_path
    )
    new_top = f"{h1_line}\n\n{compiled_truth}\n\n---" if compiled_truth else h1_line
    new_timeline = _insert_timeline_entry(timeline_section, timeline_entry, fallback_date)

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
    config: WorkspaceConfig,
    client: ModelClient,
    text: str,
    proposal: EnrichmentProposal,
    *,
    on_progress: Callable[[str], None] | None = None,
    checkpoint_hash: str | None = None,
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
            target = config.root_path / resolution.target_note_path
            if target.is_file():
                with contextlib.suppress(OSError):
                    existing = target.read_text(encoding="utf-8")
                    if _is_unpopulated_stub(existing):
                        proposal.warnings.append(
                            f"{resolution.name} is still an empty stub and this update "
                            "didn't populate it either (left below the relevance "
                            "threshold) — its founding content may be permanently "
                            "missing."
                        )
            continue
        target = config.root_path / resolution.target_note_path
        if not target.is_file():
            # Entity resolution matched against the index, which knows about
            # pages earlier sources created; the writer only sees the working
            # tree. Capturing a cluster of related sources before reviewing
            # any PRs is wakil's own model, so the target commonly lives on
            # an earlier, unmerged ingest branch (#188). Say which one.
            elsewhere = git.branches_containing(config.root_path, resolution.target_note_path)
            proposal.missing_update_targets.append(
                _MissingUpdateTarget(
                    name=resolution.name,
                    path=resolution.target_note_path,
                    branches=elsewhere,
                )
            )
            located = (
                f" — it exists on {', '.join(elsewhere)}, which hasn't been merged yet"
                if elsewhere
                else " — that file doesn't exist on disk"
            )
            proposal.warnings.append(
                f"{resolution.name}: entity resolution says update "
                f"{resolution.target_note_path}{located} — skipped"
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

    cached = (
        _load_checkpoint(config, proposal.source_id, "revision", checkpoint_hash)
        if checkpoint_hash is not None
        else None
    )
    if cached is not None:
        proposal.entity_updates.extend(
            _deserialize_entity_update(u) for u in cached["entity_updates"]
        )
        proposal.warnings.extend(cached["warnings"])
        return

    if on_progress is not None:
        count = len(candidates)
        entity_word = "entity" if count == 1 else "entities"
        on_progress(f"Revising {count} existing {entity_word}...")
    warnings_before = len(proposal.warnings)
    _revise_candidates(config, client, text, proposal, candidates)

    # Not checkpointed when a target was missing: that is not a clean
    # completion (ADR 0020's own rule), and the staleness key covers only the
    # source text, context, and model — nothing about which candidates were
    # reachable. Saving here meant the post-merge re-run this file tells the
    # user to do returned the cached payload and never revised the page that
    # had just become available: zero model calls, exit 0, nothing written,
    # source flipped to `enriched`. That is the silent no-op #188 is about,
    # reached by following #188's own remediation.
    if checkpoint_hash is not None and not proposal.missing_update_targets:
        _save_checkpoint(
            config,
            proposal.source_id,
            "revision",
            checkpoint_hash,
            client.model,
            {
                "entity_updates": [_serialize_entity_update(u) for u in proposal.entity_updates],
                "warnings": proposal.warnings[warnings_before:],
            },
        )


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
    today: str,
) -> None:
    by_path = {res.target_note_path: content for res, _, content in candidates}
    name_by_path = {res.target_note_path: res.name for res, _, _ in candidates}
    for revision in revisions:
        old_content = by_path.get(revision.target_note_path)
        if old_content is None:
            continue
        if not revision.has_update:
            # This update was declined outright — a much higher-severity case
            # than the below-relevance-threshold skip above if the entity has
            # NEVER been populated: the stub _stub_content wrote at creation
            # time is still exactly what's on disk, and this pass — like
            # every one before it — didn't fold in real content either
            # (issue #45).
            if _is_unpopulated_stub(old_content):
                name = name_by_path.get(revision.target_note_path, revision.target_note_path)
                proposal.warnings.append(
                    f"{name} is still an empty stub and this update didn't populate "
                    "it either — its founding content may be permanently missing."
                )
            continue
        new_content = _merge_entity_note(
            old_content, revision, today, proposal.source_captured_date
        )
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
                confidence=revision.confidence,
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
    targets: list[tuple[str, str]] = []
    for res, _, content in candidates:
        # candidates only ever holds action=="update" resolutions with a
        # target_note_path (filtered when candidates are built).
        assert res.target_note_path is not None
        targets.append((res.target_note_path, content))
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

    _apply_entity_revisions(proposal, candidates, result.revisions, workspace_today(config))


def _stub_content(metadata: dict, name: str) -> str:
    """Compiled Truth / Timeline skeleton per docs/entity-model.md.

    Its caller, `_build_stub_entities`, always writes this placeholder
    first; `_synthesize_stub_content` (issue #70) then tries to replace it
    with real content synthesized from the source, and falls back to this
    exact placeholder — never a fabrication — whenever the source doesn't
    support that or the synthesis call itself fails."""
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


# The Compiled Truth / Open Threads span _stub_content writes at creation
# time, independent of entity name or frontmatter (both live outside this
# span) — derived from _stub_content itself rather than duplicated as a
# second literal, so the two can never silently drift apart. Used to detect
# an entity that has NEVER been populated, so a declined update against it
# can be flagged with a much higher-severity warning than "this particular
# update was skipped" (issue #45).
_stub_sections = _split_note_sections(frontmatter_lib.loads(_stub_content({}, "_")).content)
assert _stub_sections is not None
_STUB_TOP_SECTION = _stub_sections[1]
del _stub_sections


def _is_unpopulated_stub(content: str) -> bool:
    """True if `content` (a full note file, frontmatter included) still has
    exactly the Compiled Truth placeholder `_stub_content` writes at
    creation time — i.e. nothing has ever synthesized real content into it,
    across however many enrich passes have touched the entity since."""
    try:
        post = frontmatter_lib.loads(content)
    except Exception:
        return False
    sections = _split_note_sections(post.content)
    if sections is None:
        return False
    _, top_section, _ = sections
    return top_section.strip() == _STUB_TOP_SECTION


def _entity_type_of(metadata: object) -> str | None:
    entity_type = metadata.get("type") if isinstance(metadata, dict) else None
    return entity_type if isinstance(entity_type, str) and entity_type else None


def _validate_proposed_files(
    proposed_files: list[ProposedFile],
    schemas: dict[str, EntitySchema],
    kb_root: Path | None,
) -> list[ProposalIssue]:
    """No two proposed files may share a path; every proposed new file must
    carry frontmatter valid against its entity schema and land under its
    type's canonical directory (a subdirectory is fine, e.g.
    "meetings/2026/...") — `_build_stub_entities` already routes stubs this
    way by construction, but the model-chosen primary note gets no such
    guarantee, so it's checked here. A real routing bug, not cosmetic drift,
    so this is a hard stop rather than an auto-correction — unlike a
    filename/H1 slug mismatch.
    """
    issues: list[ProposalIssue] = []
    seen_paths: set[str] = set()
    for proposed in proposed_files:
        if proposed.path in seen_paths:
            issues.append(ProposalIssue(proposed.path, "duplicate proposed path"))
        seen_paths.add(proposed.path)
        try:
            metadata = frontmatter_lib.loads(proposed.content).metadata
        except Exception:
            metadata = {}
        entity_type = _entity_type_of(metadata)
        if entity_type is None:
            issues.append(ProposalIssue(proposed.path, "proposed file has no `type:` frontmatter"))
            continue
        for error in validate_frontmatter(entity_type, metadata, kb_root):
            issues.append(ProposalIssue(proposed.path, str(error)))

        schema = schemas.get(entity_type)
        if schema is not None and schema.directory is not None:
            schema_dir = schema.directory.rstrip("/")
            proposed_dir = Path(proposed.path).parent.as_posix()
            if proposed_dir != schema_dir and not proposed_dir.startswith(f"{schema_dir}/"):
                issues.append(
                    ProposalIssue(
                        proposed.path,
                        f"type '{entity_type}' pages belong under {schema_dir}/, "
                        f"not {proposed_dir}/",
                    )
                )
    return issues


def _validate_entity_updates(
    entity_updates: list[EntityUpdate], kb_root: Path | None
) -> list[ProposalIssue]:
    """Edits to existing notes must still satisfy their type's schema —
    frontmatter_updates could otherwise merge in a value that breaks it."""
    issues: list[ProposalIssue] = []
    for update in entity_updates:
        try:
            metadata = frontmatter_lib.loads(update.new_content).metadata
        except Exception:
            issues.append(
                ProposalIssue(update.target_note_path, "merged content is not valid frontmatter")
            )
            continue
        entity_type = _entity_type_of(metadata)
        if entity_type is None:
            issues.append(
                ProposalIssue(update.target_note_path, "merged file has no `type:` frontmatter")
            )
            continue
        for error in validate_frontmatter(entity_type, metadata, kb_root):
            issues.append(ProposalIssue(update.target_note_path, str(error)))
    return issues


def _validate_unroutable_creates(
    entity_resolutions: list[EntityResolution], schemas: dict[str, EntitySchema]
) -> list[ProposalIssue]:
    """Creates that could not even build a stub: missing schema or directory."""
    issues: list[ProposalIssue] = []
    for resolution in entity_resolutions:
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


def validate_proposal(
    proposal: EnrichmentProposal, kb_root: Path | None = None
) -> list[ProposalIssue]:
    """Invariant gate between prepare and apply; any issue blocks the write.

    Implements entity-resolution.md's constraints plus the schema check:
    every proposed new file must carry frontmatter valid against its entity
    schema; a pending create for a type with no schema at all is a hard
    stop, not a best-guess write (types that have a schema but no canonical
    directory, e.g. `index`, are instead warned about and dropped from
    proposal.entity_resolutions upstream in _build_stub_entities, so this
    loop never sees them); no two proposed files may share a path. Routing
    is 1:N by construction (proposed_note + stub_entities), and content-hash
    dedup is already enforced upstream at capture time.

    `kb_root` should be the workspace root so a kb-local schema override
    validates against the same schema extraction/entity-resolution used —
    omitted only by tests that don't exercise the override mechanism.
    """
    schemas = load_entity_schemas(kb_root)

    proposed_files = list(proposal.stub_entities)
    if proposal.proposed_note is not None:
        proposed_files.insert(0, proposal.proposed_note)

    return [
        *_validate_proposed_files(proposed_files, schemas, kb_root),
        *_validate_entity_updates(proposal.entity_updates, kb_root),
        *_validate_unroutable_creates(proposal.entity_resolutions, schemas),
    ]


def _write_new_proposed_files(config: WorkspaceConfig, proposal: EnrichmentProposal) -> list[str]:
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
    return files_written


def _apply_entity_updates(
    config: WorkspaceConfig, proposal: EnrichmentProposal
) -> tuple[list[str], list[str]]:
    # Edits to existing notes: re-read immediately before writing and skip
    # (rather than overwrite blind) any file that changed since prepare —
    # mirrors schema_migrate_service.apply_migrations' stale-file guard.
    updated_files: list[str] = []
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
        updated_files.append(update.target_note_path)
    return updated_files, stale_updates_skipped


def _persist_candidate_memories(
    session: Session,
    workspace_id: int,
    user_id: int,
    source: Source,
    proposal: EnrichmentProposal,
) -> tuple[list[Memory], int]:
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
        if 0 <= rel.subject_index < len(memory_rows) and 0 <= rel.object_index < len(memory_rows):
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
    return memory_rows, relationships_created


def apply_enrichment(config: WorkspaceConfig, proposal: EnrichmentProposal) -> EnrichmentResult:
    issues = validate_proposal(proposal, config.root_path)
    if issues:
        detail = "; ".join(str(issue) for issue in issues)
        raise IngestError(f"Proposal failed validation, nothing was written: {detail}")

    files_written = _write_new_proposed_files(config, proposal)
    updated_files, stale_updates_skipped = _apply_entity_updates(config, proposal)
    files_written += updated_files

    # Before the session, so this really is a no-op: nothing reached disk (that
    # is what the empty `files_written` means) and nothing reaches the database
    # either. Committing here instead would record the memories and flip the
    # source to `enriched` — which would both duplicate those memories on the
    # re-run and force it to pass `--force`, since `prepare_enrichment` only
    # demands that of an already-enriched source. Leaving the status `raw`
    # keeps the re-run plain, and a plain re-run keeps the phase checkpoints
    # (only `--force` clears them), so it resumes instead of re-paying for the
    # model calls (#188).
    # Only when at least one target is *recoverable* — i.e. actually sits on
    # some branch. A resolution pointing at a path that exists nowhere (a
    # wrong model-produced path, or a stale index row for a deleted note —
    # the case the warning in `_run_entity_updates` was originally written
    # for) can never be resolved by merging, so hard-aborting on it is a
    # permanent dead end whose only escape is `--force`, which the message
    # correctly tells the user not to use. Those stay warnings.
    recoverable = [t for t in proposal.missing_update_targets if t.branches]
    if not files_written and recoverable:
        raise MissingUpdateTargetsError(recoverable)

    with open_session(config) as session:
        workspace_id, user_id = _require_workspace_ids(session, config)
        source = session.get(Source, proposal.source_id)
        if source is None:
            raise IngestError(f"No source with id {proposal.source_id}.")

        memory_rows, relationships_created = _persist_candidate_memories(
            session, workspace_id, user_id, source, proposal
        )

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
        indexed = index_notes(
            session, workspace_id, config.root_path, prune=not config.is_linked_worktree
        )
        session.commit()

        # The resume window this feature exists for is closed once the
        # source is actually enriched -- clear it. A declined preview or a
        # failed validate_proposal() (the raise above) never reaches here,
        # so checkpoints deliberately survive both of those paths.
        _clear_checkpoints(config, source.id)

        return EnrichmentResult(
            source_id=source.id,
            ingest_run_id=run.id,
            files_written=files_written,
            memories_created=len(memory_rows),
            relationships_created=relationships_created,
            stale_updates_skipped=stale_updates_skipped,
            sources_relinked=indexed.sources_relinked,
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
    today = workspace_today(config)
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
    today = workspace_today(config)
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
    update: EntityUpdate, compiled_truth: str, today: str
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
# mentioned in speech ("let's meet at 3:30") survive. The `[*_]` lookarounds
# keep an emphasised marker intact: `**[00:36]**` is a deliberate authored
# turn label, and stripping just the brackets' contents left a bare `****`
# behind (#179). An unemphasised `[00:36]` is still ASR noise and still goes.
_BRACKET_TS_RE = re.compile(r"(?<![*_])[\[(]\d{1,2}:\d{2}(:\d{2})?([,.]\d{1,3})?[\])](?![*_])")
# Frozen history, not a variant to keep in sync: the pattern as it stood
# before #179 added the lookarounds above. `prepare_capture` reproduces the
# content hash a transcript already in `sources/` was stored under, and that
# hash is a function of what the cleaner *used to* do -- computing it from
# the current pattern makes the check silently inert for exactly the files
# it exists to recognise (emphasised turn labels), and makes any test of it
# self-confirming. Never "fix" this to match `_BRACKET_TS_RE`.
_LEGACY_BRACKET_TS_RE = re.compile(r"[\[(]\d{1,2}:\d{2}(:\d{2})?([,.]\d{1,3})?[\])]")
_LEADING_TS_RE = re.compile(r"^\s*\d{1,2}:\d{2}(:\d{2})?([,.]\d{1,3})?\s*[-–>]*\s*")


def clean_transcript(raw: str, *, bracket_re: re.Pattern[str] = _BRACKET_TS_RE) -> str:
    """Light, deterministic transcript cleanup.

    Removes timestamp noise and normalizes whitespace without touching the
    spoken content — the raw capture must stay faithful to the source, so no
    model rewriting happens here.

    `bracket_re` is only ever overridden with `_LEGACY_BRACKET_TS_RE`, to
    recompute a pre-#179 content hash for the dedup check.
    """
    cleaned_lines: list[str] = []
    for line in raw.splitlines():
        line = bracket_re.sub("", line)
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


def _whisper_recorded_at(raw_seconds: object, config: WorkspaceConfig) -> str | None:
    """The local calendar date a recording was made.

    Apple's `dateCreated` is an absolute instant (NSDate seconds), so
    `.date()` on it yields the *UTC* day -- an evening US-Eastern recording
    would be filed a day late, and `meeting_date` beats `created` for the
    filename prefix, so the wrong date would reach both the frontmatter and
    the path (#174).
    """
    if not isinstance(raw_seconds, int | float):
        return None
    try:
        return workspace_date(config, _WHISPER_EPOCH + timedelta(seconds=raw_seconds))
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


def parse_whisper_transcript(file: Path, config: WorkspaceConfig) -> tuple[str, str | None]:
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

    recorded_at = _whisper_recorded_at(data.get("dateCreated"), config)
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


@dataclass
class ParsedInput:
    """An input file split into its own frontmatter, body, and H1 (if any).

    A hand-authored `.md` — a cleaned transcript with a real title, date,
    tags, and company already set — is user knowledge. Capture used to wrap
    such a file whole, producing a note with two frontmatter blocks and two
    H1s where every outer field was empty or a day off (#172). `body` keeps
    its H1 in place so the file is never restructured; `h1` is extracted
    separately only so the destination slug can be derived from the note's
    own title rather than the scratch file's basename.
    """

    metadata: dict
    body: str
    h1: str | None
    # Set when a leading `---` block was rejected by `_is_frontmatter`, so
    # "I captured your file verbatim" is visible in the preview rather than
    # inferred from the output.
    warning: str | None = None

    @property
    def is_authored_markdown(self) -> bool:
        """Frontmatter, or a *title* H1 — not any heading anywhere.

        `h1` is only set for a leading H1 (see `_split_authored_markdown`),
        because "contains a `# ` line" is far too weak a signal: an ASR dump
        with a mid-document section heading would otherwise count as authored,
        skip `clean_transcript` entirely, and take its destination slug from
        that section heading.
        """
        return bool(self.metadata) or self.h1 is not None


def _split_authored_markdown(raw: str) -> ParsedInput:
    """Parse an input's own frontmatter and *leading* H1.

    Unparseable YAML is not an error: the file is simply treated as
    unauthored prose, exactly as before. Neither is a leading `---` block
    that parses fine but isn't frontmatter -- see `_is_frontmatter`.
    """
    try:
        post = frontmatter_lib.loads(raw)
        metadata = dict(post.metadata)
        body = post.content
    except Exception:
        metadata, body = {}, raw
    if _is_frontmatter(metadata):
        return ParsedInput(metadata=metadata, body=body, h1=_leading_h1(body))
    # Not this file's frontmatter, so `post.content` is not this file's body
    # -- keep the input verbatim rather than trusting the split.
    warning = None
    if raw.lstrip().startswith("---"):
        warning = (
            "Read the leading `---` block as content, not frontmatter "
            "(its keys don't look like frontmatter keys). Nothing was "
            "parsed away — but for a transcript, normal cleanup still ran."
        )
    return ParsedInput(metadata={}, body=raw, h1=_leading_h1(raw), warning=warning)


# A frontmatter key by convention: lowercase, no whitespace. Deliberately a
# shape test rather than a list of known field names -- the point is to admit
# a user's own vault vocabulary (`attendees:`, `summary:`) as readily as
# wakil's, without duplicating `source.yaml` or going stale when a kb-local
# override adds a field.
_FRONTMATTER_KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")


def _is_frontmatter(metadata: dict) -> bool:
    """Does a parsed leading `---` block actually look like frontmatter?

    `python-frontmatter` doesn't raise on a leading `---` that was never a
    fence -- it splits on it regardless of what sits between, which loses
    content in both directions:

    - A block parsing to a *non-mapping* ("a scratch note to self") yields
      `metadata == {}` and a `.content` that has already dropped that text.
      Capture would write the shortened body out with no warning, and with
      no `legacy_text` fallback either, so a re-ingest lands as a second,
      shorter source (working-agreement item 12).
    - A block parsing to a *mapping* (`Speaker 1: hello`) moves transcript
      dialogue into frontmatter, and -- because that counts as authored --
      skips `clean_transcript` for the rest of the file. `validate_frontmatter`
      tolerates unknown keys by design, so nothing downstream objects.

    A transcript opening with a `---` rule is a plausible hand-cleaned
    artifact, i.e. squarely this feature's input population.

    Requiring *every* key to be frontmatter-shaped rejects both shapes above
    while admitting any hand-authored file whose keys follow the convention,
    including vault-specific ones this code has never heard of. The residue
    is a block of all-lowercase single-word speaker labels (`alice: hi`),
    which is read as frontmatter; and a file whose real frontmatter uses
    capitalised keys, which is captured as content. Only the second is
    signalled (`ParsedInput.warning`) -- the first is indistinguishable from
    frontmatter here, and is visible on the CLI only because the preview
    renders the whole file (#235). The content direction is the safe one: it
    restores the pre-#172 double-wrap rather than moving content out of the
    body.
    """
    return bool(metadata) and all(
        isinstance(key, str) and _FRONTMATTER_KEY_RE.fullmatch(key) for key in metadata
    )


def _leading_h1(body: str) -> str | None:
    """The H1 only when it is the document's first non-blank line.

    A title H1 says "this file was authored"; a section heading halfway down
    says nothing of the kind."""
    for line in body.splitlines():
        if not line.strip():
            continue
        match = _H1_RE.match(line)
        return (match.group(0).lstrip("#").strip() or None) if match else None
    return None


def _authored_slug_source(parsed: ParsedInput) -> str | None:
    """The note's own title, for the destination filename. Any leading date is
    stripped because `_build_raw_file` prepends one itself."""
    for candidate in (parsed.metadata.get("title"), parsed.metadata.get("name"), parsed.h1):
        if isinstance(candidate, str) and candidate.strip():
            stripped = _LEADING_DATE_RE.sub("", candidate.strip()) or candidate.strip()
            if stripped.strip():
                return stripped.strip()
    return None


def _authored_meeting_date(parsed: ParsedInput) -> str | None:
    """An explicit *meeting* date the author already set beats anything
    inferred.

    Deliberately not `captured`: that is when the file was captured, not when
    the meeting happened -- `_KNOWN_FIELD_VALUES` maps it to `created`, and
    `source.yaml`'s header says the same. Reading it here overrode a correct
    inferred date (and with it the destination filename) with today's, and
    `meeting_date` is durable: it seeds `Source.metadata_json` and the
    Timeline heading fallback (#77).
    """
    for key in ("meeting_date", "date"):
        value = parsed.metadata.get(key)
        if isinstance(value, datetime):
            # `datetime` subclasses `date`, so isoformat() would carry a time
            # component and fail the ISO-date match below.
            text = value.date().isoformat()
        elif isinstance(value, date):
            text = value.isoformat()
        else:
            text = value
        if isinstance(text, str) and _ISO_DATE_RE.fullmatch(text.strip()):
            return text.strip()
    return None


# Frontmatter keys wakil owns on a raw capture, which an authored file must
# not overwrite. `type` and `source_type` are what routing and validation key
# on; `origin`/`url`/`source_file` record where the capture actually came
# from; `status` is lifecycle state that only wakil advances (working
# agreement item 9 -- a brand-new capture is `raw`, whatever the author's
# original file claimed).
_WAKIL_OWNED_FRONTMATTER = frozenset(
    {"type", "source_type", "status", "origin", "origin_kind", "url", "source_file"}
)


def _merge_authored_metadata(
    generated: dict, authored: dict, kb_root: Path | None = None
) -> tuple[dict, list[str]]:
    """Merge an input's own frontmatter over wakil's generated fields.

    Authored values win where they actually say something -- an empty
    authored value must not clobber a real generated one -- except for the
    keys wakil owns (`_WAKIL_OWNED_FRONTMATTER`), and except where the value
    would make the note fail its own schema.

    That last check matters because hand-authored files are exactly this
    feature's input population, and `docs/TROUBLESHOOTING.md` already records
    the mistake they make: an unquoted `[[wikilink]]` in YAML parses as a
    nested list, so `title: [[people/jane]]` becomes `[['people/jane']]`.
    Writing that through unchecked produces a capture the project's own
    `wakil schema validate` rejects on arrival.

    Returns the merged metadata plus a list of human-readable notes about
    anything skipped, for the preview.
    """
    merged = dict(generated)
    skipped: list[str] = []
    label_field = "title" if "title" in generated else None

    for key, value in authored.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if isinstance(value, list | dict) and not value:
            continue
        if key in _WAKIL_OWNED_FRONTMATTER:
            skipped.append(f"{key}: wakil records this itself for a raw capture")
            continue
        # `source.yaml` is a document-category schema, so it takes `title`
        # and rejects a present `name`. `prepare_capture` already treats the
        # two as aliases; without this the merge writes both and every
        # `name:`-keyed input arrives non-conformant.
        if key == "name" and label_field:
            merged.setdefault(label_field, value)
            continue
        merged[key] = value

    if kb_root is not None:
        merged, dropped = _drop_schema_violating_keys(merged, generated, kb_root)
        skipped.extend(dropped)
    return merged, skipped


def _drop_schema_violating_keys(
    merged: dict, generated: dict, kb_root: Path
) -> tuple[dict, list[str]]:
    """Fall back to the generated value for any key whose authored value the
    `source` schema rejects. Better a correct capture with a note than a
    written-out file that fails validation the moment anyone checks."""
    errors = validate_frontmatter("source", merged, kb_root)
    if not errors:
        return merged, []
    dropped: list[str] = []
    for error in errors:
        field = getattr(error, "field", None)
        if not field or field not in merged or merged.get(field) == generated.get(field):
            continue
        if field in generated:
            merged[field] = generated[field]
        else:
            del merged[field]
        dropped.append(str(error))  # SchemaError already prefixes the field
    return merged, dropped


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
    created = workspace_today(config)
    directory = Path(config.ingest_directory) / RAW_DIRS.get(proposal.source_type, "clippings")
    # slug_source already had any leading date stripped (for file-derived
    # captures, in prepare_capture; article titles never carry one to begin
    # with) -- re-stripping here would double-apply _LEADING_DATE_RE and, for
    # a source filename that was itself nothing but a date, collapse an
    # already-empty-then-restored stem to "untitled" a second time (#79).
    # slugify() itself still falls back to "untitled" for genuinely
    # content-free input (e.g. an all-punctuation title).
    slug = slugify(slug_source)
    base = f"{proposal.meeting_date or created}-{slug}"
    # Deliberately not `_unused_path` here: silently sliding to `<base>-1.md`
    # is how two near-duplicate transcripts for one recording ended up in a
    # vault, one of them invisibly shadowed (#173). Record the collision and
    # let `apply_capture` refuse. `_unused_path`'s other caller
    # (`_sanitize_note`) legitimately does want silent disambiguation.
    path = directory / f"{base}.md"
    if (config.root_path / path).exists():
        proposal.collision = str(path)

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

    # The input's own frontmatter is authoritative where it says something
    # (#172): wakil fills the gaps rather than wrapping a second, emptier
    # block around a better-populated one. Provenance and lifecycle fields
    # stay wakil's, and anything the `source` schema rejects falls back to
    # the generated value rather than being written out invalid.
    if proposal.authored_metadata:
        metadata, skipped = _merge_authored_metadata(
            metadata, proposal.authored_metadata, config.root_path
        )
        for note in skipped:
            proposal.warnings.append(f"Ignored the input's own {note}")

    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)
    body = proposal.text
    # Only add an H1 when the input didn't bring its own -- `proposal.text` is
    # the body with its authored H1 still in place.
    if proposal.source_type == "transcript" and proposal.authored_h1 is None:
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


def _sanitize_note(
    config: WorkspaceConfig, note: ProposedFile, proposal: "EnrichmentProposal"
) -> ProposedFile:
    """Keep the model-proposed primary note inside the workspace,
    collision-free, and slug-consistent with itself.

    The path-safety checks below are unchanged; what's new is slug drift
    correction, matching the invariant `_build_stub_entities` already gets
    right for stub pages: a page's filename is `slugify()` of its own
    displayed name, never the model's freehand choice. `title`/`name`
    frontmatter and the H1 heading stay exactly as authored (both are
    legitimate human-cased display text, per note-conformance/SKILL.md) --
    only the filename is re-derived when it disagrees with slugify(H1), or,
    absent an H1, when it isn't already slugify()-equivalent to itself. Any
    leading date prefix (`_LEADING_DATE_RE`, the same convention capture's
    own dated filenames use) is preserved rather than folded into that
    comparison -- a single-occurrence note like a meeting legitimately
    carries a date the H1 doesn't, and that's not drift. Auto-correcting is
    chosen over a hard ProposalIssue stop (mirrors how
    `_reconcile_entity_links` already silently repairs proposed-note
    wikilink drift): a filename typo/verbosity from the model is exactly
    the kind of mechanical drift this codebase already auto-fixes rather
    than bouncing the whole enrichment back to the user for. Every
    correction is still recorded in proposal.warnings, never applied
    invisibly.

    Directory placement is corrected here too, rather than left as a hard
    stop. `type: source` -> `sources/` is a pure function of the `type:`
    field, so treating a mismatch as a fatal routing bug threw away an entire
    enrichment run -- every model call included -- over something
    mechanically derivable, while the filename half of the very same path was
    already being auto-corrected (#187). `validate_proposal` still hard-stops
    when the type has no schema directory to route to, which is a genuinely
    unresolvable case.
    """
    root = config.root_path.resolve()
    candidate = Path(note.path)

    valid = (
        not candidate.is_absolute()
        and candidate.suffix == ".md"
        and (root / candidate).resolve().is_relative_to(root)
    )
    if not valid or (root / candidate).exists():
        # Routing unclear or collision: fall back to the note's own type's
        # canonical directory, and only to `drafts/` when it hasn't got one.
        # Dumping a typed page into `drafts/` regardless of its `type:` is
        # what manufactured #187's unwinnable "type 'source' pages belong
        # under sources/, not drafts/" failure.
        directory = _schema_directory_for_note(config, note) or config.generated_directory
        candidate = _unused_path(root, Path(directory), slugify(proposal.title))
        # This branch used to move the note silently, which contradicted this
        # function's own docstring and, since #187 stopped routing here to
        # `drafts/`, dropped the page beside real pages of its type instead of
        # in a marked "needs attention" bucket. The collision case is the one
        # that matters: a same-slug page under the canonical directory is
        # quite possibly the same entity, which is #186's failure mode.
        if not valid:
            proposal.warnings.append(
                f"The proposed note's path {note.path!r} was unusable (outside the "
                f"knowledge base, or not a .md file), so it was placed at "
                f"{candidate.as_posix()} instead. Model-proposed paths derive from "
                f"ingested text, so a repeat of this is worth looking at."
            )
        else:
            proposal.warnings.append(
                f"{note.path} already exists, so the proposed note was placed at "
                f"{candidate.as_posix()} instead. Check whether the existing page is "
                f"the same subject — if it is, merge them rather than keeping both."
            )
        return _retarget_self_links(note, str(candidate))

    # Each corrector below decides a path and records its own warning; the
    # note's self-referential wikilinks are repointed once, at the end,
    # against the path the model actually proposed. Retargeting inside each
    # mover instead meant every mover had to remember to do it, and one of
    # three didn't — leaving a link at the pre-move path, dangling.
    candidate = _route_to_schema_directory(config, candidate, note, proposal)
    candidate = _reslug_target(candidate, note.content, proposal)
    candidate = _free_target(root, candidate, proposal)
    return _retarget_self_links(note, str(candidate))


def _schema_directory_for_note(config: WorkspaceConfig, note: ProposedFile) -> str | None:
    """The canonical directory for whatever `type:` a proposed note declares."""
    try:
        metadata = frontmatter_lib.loads(note.content).metadata
    except Exception:
        return None
    if not isinstance(metadata, dict):
        return None
    entity_type = _entity_type_of(metadata)
    if entity_type is None:
        return None
    schema = load_entity_schemas(config.root_path).get(entity_type)
    if schema is None or schema.directory is None:
        return None
    return schema.directory.rstrip("/")


def _route_to_schema_directory(
    config: WorkspaceConfig,
    candidate: Path,
    note: ProposedFile,
    proposal: "EnrichmentProposal",
) -> Path:
    """Move a proposed note under its type's canonical directory, warning.

    Symmetric with `_reslug_proposed_note`'s filename correction: both are
    single-valued, mechanically derivable fixes, and neither should cost the
    caller a whole re-run (#187). A subdirectory of the canonical directory
    is left alone -- `meetings/2026/...` is deliberate."""
    schema_dir = _schema_directory_for_note(config, note)
    if schema_dir is None:
        return candidate
    current = candidate.parent.as_posix()
    if current == schema_dir or current.startswith(f"{schema_dir}/"):
        return candidate
    corrected = Path(schema_dir) / candidate.name
    proposal.warnings.append(
        f"Moved the proposed note from {candidate.as_posix()} to {corrected.as_posix()} "
        f"to match its own `type:` (pages of that type belong under {schema_dir}/)"
    )
    return corrected


def _reslug_target(candidate: Path, content: str, proposal: "EnrichmentProposal") -> Path:
    """Re-derive the filename from the note's own H1, leaving any leading date."""
    try:
        body = frontmatter_lib.loads(content).content
    except Exception:
        body = content

    stem = candidate.stem
    prefix_match = _LEADING_DATE_RE.match(stem)
    prefix = prefix_match.group(0) if prefix_match else ""
    rest = stem[len(prefix) :] or stem  # never strip a date-only stem down to nothing

    h1_match = _H1_RE.search(body)
    h1_text = h1_match.group(0).lstrip("#").strip() if h1_match else None
    target_rest = slugify(h1_text) if h1_text else slugify(rest)
    if target_rest == rest:
        return candidate

    corrected = candidate.with_name(f"{prefix}{target_rest}{candidate.suffix}")
    proposal.warnings.append(
        f"Corrected the proposed note's filename from {candidate.as_posix()} to "
        f"{corrected.as_posix()} to match slugify() (the same convention new entity "
        "stub pages already use)"
    )
    return corrected


def _retarget_self_links(proposed: ProposedFile, new_path: str) -> ProposedFile:
    """Move a proposed note to `new_path`, repointing the wikilinks in its own
    body that referred to it under the path the model originally proposed."""
    old_path = proposed.path
    if _normalize_link_path(old_path) == _normalize_link_path(new_path):
        return ProposedFile(path=new_path, content=proposed.content)

    def _replace(match: re.Match) -> str:
        link_path = match.group(1).strip()
        display = match.group(2)
        if _normalize_link_path(link_path) != _normalize_link_path(old_path):
            return match.group(0)
        return f"[[{new_path}|{display}]]" if display is not None else f"[[{new_path}]]"

    return ProposedFile(path=new_path, content=_WIKILINK_RE.sub(_replace, proposed.content))


def _free_target(root: Path, candidate: Path, proposal: "EnrichmentProposal") -> Path:
    """Re-check the destination after routing and re-slugging have moved it.

    `_sanitize_note`'s collision check runs against the path the model
    proposed; `_route_to_schema_directory` and `_reslug_target` then rewrite
    the directory and the filename, so the path actually written was never
    checked against the filesystem. A corrected path landing on an existing
    file killed the run in `apply_enrichment` -- after every model call had
    already been paid for -- with "Refusing to overwrite existing file". That
    is #187's own defect, relocated by #187's fix.
    """
    if not (root / candidate).exists():
        return candidate
    free = _unused_path(root, candidate.parent, candidate.stem)
    proposal.warnings.append(
        f"{candidate.as_posix()} already exists, so the corrected note was proposed at "
        f"{free.as_posix()} instead"
    )
    return free


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
