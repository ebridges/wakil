"""Bring existing vault frontmatter into conformance with the entity schemas.

Cheap tier only (docs/ingestion-refactor-spec.md §4): mechanical fixes a
linter could make — casing/naming duplicate normalization, retyping
organization/ files that borrowed concept's schema, and repairing naive
title-caser artifacts on the name/title pair. The expensive tier
(learning-agenda retyping, the reflections move) is a later phase.

Same propose → diff → confirm discipline as ingest: planning touches
nothing; apply verifies each file is unchanged since planning before
rewriting it, and ambiguous cases (e.g. `author` and `authors` present with
different values) are surfaced and skipped, never silently resolved.
"""

import difflib
import re
from dataclasses import dataclass, field

import yaml
from sqlalchemy import select

from wakil.app.workspace_service import index_notes, open_session
from wakil.config.settings import WorkspaceConfig
from wakil.schema.loader import load_entity_schemas
from wakil.storage.schema import Note, Workspace

# Canonical spellings for casing/naming duplicates, from the
# entity-metadata.md census ("small but should be cleaned up mechanically").
FIELD_RENAMES = {
    "end_date": "end-date",
    "start_date": "start-date",
    "linkedin-link": "linkedin",
    "authors": "author",
    "link": "url",
}

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
_QUOTED_TYPE_RE = re.compile(r"""^type:\s*["']""", re.MULTILINE)


class MigrateError(RuntimeError):
    pass


@dataclass
class MigrationProposal:
    path: str  # workspace-relative
    entity_type: str
    fixes: list[str]
    old_content: str
    new_content: str

    def diff(self) -> str:
        return "".join(
            difflib.unified_diff(
                self.old_content.splitlines(keepends=True),
                self.new_content.splitlines(keepends=True),
                fromfile=f"a/{self.path}",
                tofile=f"b/{self.path}",
            )
        )


@dataclass
class MigrationPlan:
    # Proposals grouped by effective entity type, for per-type confirmation.
    by_type: dict[str, list[MigrationProposal]] = field(default_factory=dict)
    # Files examined but left alone for a stated reason (ambiguities).
    skipped: list[str] = field(default_factory=list)

    @property
    def total_files(self) -> int:
        return sum(len(proposals) for proposals in self.by_type.values())


def plan_schema_migration(config: WorkspaceConfig, entity_type: str | None = None) -> MigrationPlan:
    """Walk indexed notes and propose cheap-tier frontmatter fixes."""
    schemas = load_entity_schemas()
    if entity_type is not None and entity_type not in schemas:
        raise MigrateError(
            f"No entity schema defines type '{entity_type}' (known: {', '.join(sorted(schemas))})"
        )

    plan = MigrationPlan()
    with open_session(config) as session:
        workspace_id = session.scalar(
            select(Workspace.id).where(Workspace.root_path == str(config.root_path))
        )
        if workspace_id is None:
            raise MigrateError("Workspace database is not initialized; run `wakil init` first.")
        paths = list(
            session.scalars(
                select(Note.path).where(Note.workspace_id == workspace_id).order_by(Note.path)
            )
        )

    for rel_path in paths:
        result = _plan_file(config, rel_path, schemas, entity_type)
        if result is None:
            continue
        proposal, notes = result
        plan.skipped.extend(notes)
        if proposal is not None:
            plan.by_type.setdefault(proposal.entity_type, []).append(proposal)
    return plan


def _plan_file(
    config: WorkspaceConfig,
    rel_path: str,
    schemas: dict,
    only_type: str | None,
) -> tuple[MigrationProposal | None, list[str]] | None:
    full_path = config.root_path / rel_path
    try:
        old_content = full_path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _FRONTMATTER_RE.match(old_content)
    if match is None:
        return None
    yaml_text = match.group(1)
    body = old_content[match.end() :]
    try:
        metadata = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return None
    if not isinstance(metadata, dict) or "type" not in metadata:
        return None

    fixes: list[str] = []
    notes: list[str] = []
    metadata = dict(metadata)

    # Fix: organization/ files that borrowed concept's schema.
    declared_type = str(metadata.get("type") or "")
    effective_type = declared_type
    if declared_type == "concept" and rel_path.split("/", 1)[0] == "organization":
        metadata["type"] = effective_type = "organization"
        fixes.append("retype organization/ file from `type: concept` to `type: organization`")

    if effective_type not in schemas:
        return None  # unknown types are out of scope for the cheap tier
    if only_type is not None and effective_type != only_type:
        return None

    # Fix: casing/naming duplicates.
    for old_name, new_name in FIELD_RENAMES.items():
        if old_name not in metadata:
            continue
        if new_name in metadata:
            if metadata[old_name] == metadata[new_name]:
                del metadata[old_name]
                fixes.append(f"drop `{old_name}` (exact duplicate of `{new_name}`)")
            else:
                notes.append(
                    f"{rel_path}: `{old_name}` and `{new_name}` both present with "
                    "different values — left for manual review"
                )
        else:
            metadata = {new_name if k == old_name else k: v for k, v in metadata.items()}
            fixes.append(f"rename `{old_name}` to `{new_name}`")

    # Fix: naive title-caser artifacts — title is a mechanical re-case of
    # name (same string case-insensitively) that broke authored casing
    # ("1NSP" -> "1nsp"). name carries the authored form; align title to it.
    # A genuinely distinct title ("Chapter 0: Readme" vs "README") differs
    # case-insensitively and is never touched.
    name, title = metadata.get("name"), metadata.get("title")
    if (
        isinstance(name, str)
        and isinstance(title, str)
        and name != title
        and name.strip().lower() == title.strip().lower()
    ):
        metadata["title"] = name
        fixes.append("align mechanically re-cased `title` with authored `name`")

    # Fix: quoted `type: "source"` style — normalized by re-serialization.
    if _QUOTED_TYPE_RE.search(yaml_text):
        fixes.append("normalize quoted `type:` value")

    if not fixes:
        return None if not notes else (None, notes)

    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)
    new_content = f"---\n{frontmatter}---\n{body}"
    if new_content == old_content:
        return None if not notes else (None, notes)
    return (
        MigrationProposal(
            path=rel_path,
            entity_type=effective_type,
            fixes=fixes,
            old_content=old_content,
            new_content=new_content,
        ),
        notes,
    )


def apply_migrations(
    config: WorkspaceConfig, proposals: list[MigrationProposal]
) -> tuple[list[str], list[str]]:
    """Rewrite confirmed files; returns (written paths, stale-skip messages).

    A file that changed on disk since planning is skipped, not overwritten —
    the plan's diff no longer describes what would happen.
    """
    written: list[str] = []
    stale: list[str] = []
    for proposal in proposals:
        full_path = config.root_path / proposal.path
        try:
            current = full_path.read_text(encoding="utf-8")
        except OSError as exc:
            stale.append(f"{proposal.path}: unreadable ({exc}); skipped")
            continue
        if current != proposal.old_content:
            stale.append(f"{proposal.path}: changed on disk since planning; skipped")
            continue
        full_path.write_text(proposal.new_content, encoding="utf-8")
        written.append(proposal.path)

    if written:
        with open_session(config) as session:
            workspace_id = session.scalar(
                select(Workspace.id).where(Workspace.root_path == str(config.root_path))
            )
            if workspace_id is not None:
                index_notes(session, workspace_id, config.root_path)
                session.commit()
    return written, stale
