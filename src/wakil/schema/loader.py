"""Load entity schemas from schema/entities/*.yaml into typed models.

Resolution mirrors `wakil.skills.resolver` (docs/skill-resolution-specification.md),
applied per type-file instead of per skill-directory: an ordered list of
roots (`WAKIL_SCHEMA_PATH` override, kb-local `schema/entities/`, user-level
config, built-in) is searched, and for each entity `type`, the first root
that defines it wins — whole-file, no merging across roots. This supersedes
the earlier decision (docs/ingestion-refactor-spec.md) that entity-type
extension only happens by forking wakil's own source tree; a kb-local or
user-level override now works the same way a skill override does. Loading is
cached per resolved root set, so repeated validation during one command
parses the files once.
"""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from wakil.config.registry import config_home

DEFAULT_SCHEMA_DIR = Path(__file__).parent / "entities"

SOURCE_OVERRIDE = "override"
SOURCE_KB_LOCAL = "kb-local"
SOURCE_USER = "user"
SOURCE_BUILTIN = "builtin"

FieldKind = Literal["string", "list", "ref", "enum", "date", "bool", "int"]
Category = Literal["identity", "document", "hybrid"]


class SchemaLoadError(RuntimeError):
    pass


class FieldSpec(BaseModel):
    required: bool = False
    kind: FieldKind
    values: list[str] | None = None  # enum only
    ref_type: str | None = None  # ref only

    @model_validator(mode="after")
    def _check_kind_options(self) -> "FieldSpec":
        if self.kind == "enum" and not self.values:
            raise ValueError("enum fields must declare `values`")
        if self.kind != "enum" and self.values:
            raise ValueError("`values` is only valid on enum fields")
        if self.kind != "ref" and self.ref_type:
            raise ValueError("`ref_type` is only valid on ref fields")
        return self


class EntitySchema(BaseModel):
    type: str
    # A higher-priority root (kb-local/user/override) can set this to
    # suppress a type entirely from the effective catalog — e.g. a vault
    # that wants to route what would be a built-in `concept`/`project`/etc.
    # to its own kb-local type instead, rather than merely redefining the
    # built-in one (see `_load_cached`). A disabled marker file only needs
    # `type` + `disabled`; the other fields are meaningless for a type that
    # won't appear in the catalog, so `_check_category_conventions` skips
    # its checks below when this is set.
    disabled: bool = False
    directory: str | None = None  # canonical directory, None = no single home
    category: Category | None = None
    # Which body-shape template this type's proposed notes should follow
    # (a name, resolved to actual template prose by
    # `resolve_page_shape_template` — see that function's docstring for why
    # this is a separate axis from `category`: category drives the
    # name/title frontmatter rule, page_shape drives narrative structure,
    # and they don't always agree — `organization` and `project` are both
    # "hybrid" category but one is single-occurrence and the other
    # accumulates).
    page_shape: str | None = None
    fields: dict[str, FieldSpec] = Field(default_factory=dict)
    # Per-origin additive sub-schemas (source only), keyed by the base
    # `origin` enum value.
    origins: dict[str, dict[str, FieldSpec]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_category_conventions(self) -> "EntitySchema":
        # A disabled-marker file exists only to suppress a type from the
        # effective catalog — it never gets validated as a usable schema,
        # so none of the category/page_shape/fields conventions apply.
        if self.disabled:
            return self
        if self.category is None:
            raise ValueError(f"{self.type}: `category` is required unless `disabled: true`")
        if self.page_shape is None:
            raise ValueError(f"{self.type}: `page_shape` is required unless `disabled: true`")
        # The identity/document/hybrid split *is* the name/title rule
        # (entity-metadata.md, cross-cutting findings): a schema that
        # contradicts its own category is a transcription bug.
        if self.category == "identity" and "title" in self.fields:
            raise ValueError(f"{self.type}: identity types must not define `title`")
        if self.category == "document" and "name" in self.fields:
            raise ValueError(f"{self.type}: document types must not define `name`")
        if self.category in ("identity", "hybrid"):
            spec = self.fields.get("name")
            if spec is None or not spec.required:
                raise ValueError(f"{self.type}: {self.category} types must require `name`")
        return self


@dataclass(frozen=True)
class SchemaRoot:
    """One usable, existing entity-schema root directory."""

    path: Path
    source: str


@dataclass(frozen=True)
class SchemaResolutionContext:
    """Inputs needed to resolve entity schemas: kb root plus override roots."""

    kb_root: Path | None
    user_schema_root: Path
    builtin_schema_root: Path
    schema_path: str | None = None


def parse_schema_path(value: str | None) -> list[Path]:
    """Split a `WAKIL_SCHEMA_PATH`-style value on the platform path separator."""
    if not value:
        return []
    return [Path(segment) for segment in value.split(os.pathsep) if segment]


def _normalize_root(raw: Path) -> Path:
    expanded = os.path.expandvars(str(raw))
    return Path(expanded).expanduser().resolve()


def resolve_schema_roots(context: SchemaResolutionContext) -> list[SchemaRoot]:
    """Ordered, deduplicated, existing schema roots: override -> kb-local ->
    user -> built-in. Missing or non-directory roots are silently dropped,
    mirroring `wakil.skills.resolver.resolve_roots`."""
    raw_entries: list[tuple[Path, str]] = [
        (path, SOURCE_OVERRIDE) for path in parse_schema_path(context.schema_path)
    ]
    if context.kb_root is not None:
        raw_entries.append((context.kb_root / "schema" / "entities", SOURCE_KB_LOCAL))
    raw_entries.append((context.user_schema_root, SOURCE_USER))
    raw_entries.append((context.builtin_schema_root, SOURCE_BUILTIN))

    seen: set[Path] = set()
    roots: list[SchemaRoot] = []
    for raw_path, source in raw_entries:
        normalized = _normalize_root(raw_path)
        if normalized in seen:
            continue
        seen.add(normalized)
        if normalized.is_dir():
            roots.append(SchemaRoot(path=normalized, source=source))
    return roots


def default_schema_context(
    kb_root: Path | None = None, *, environ: dict[str, str] | None = None
) -> SchemaResolutionContext:
    """The standard `SchemaResolutionContext` for a knowledge base (or none)."""
    env = environ if environ is not None else os.environ
    return SchemaResolutionContext(
        kb_root=kb_root,
        user_schema_root=config_home() / "schema" / "entities",
        builtin_schema_root=DEFAULT_SCHEMA_DIR,
        schema_path=env.get("WAKIL_SCHEMA_PATH"),
    )


def load_entity_schemas(
    kb_root: Path | None = None, *, context: SchemaResolutionContext | None = None
) -> dict[str, EntitySchema]:
    """All entity schemas keyed by `type`, kb-local/user overriding built-in.

    `kb_root`, when given, is the workspace root (its `schema/entities/`
    subdirectory is one of the search roots) — not a raw schema directory.
    Pass an explicit `context` for full control (tests, `WAKIL_SCHEMA_PATH`).
    """
    ctx = context or default_schema_context(kb_root)
    roots = resolve_schema_roots(ctx)
    return _load_cached(tuple((root.path, root.source) for root in roots))


def resolve_entity_schema(
    entity_type: str, kb_root: Path | None = None, *, context: SchemaResolutionContext | None = None
) -> tuple[EntitySchema, SchemaRoot] | None:
    """The schema for `entity_type` plus which root won it, for CLI diagnostics."""
    ctx = context or default_schema_context(kb_root)
    for root in resolve_schema_roots(ctx):
        for schema in _load_root(root.path).values():
            if schema.type == entity_type:
                return schema, root
    return None


@lru_cache(maxsize=32)
def _load_cached(roots: tuple[tuple[Path, str], ...]) -> dict[str, EntitySchema]:
    by_type: dict[str, EntitySchema] = {}
    # Types disabled by a higher-priority root stay excluded even if a
    # lower-priority root (typically built-in) defines them later — a
    # kb-local `disabled: true` marker suppresses the type outright rather
    # than just redefining it (issue #38).
    suppressed: set[str] = set()
    for path, _source in roots:
        for schema in _load_root(path).values():
            # Whole-file, first-root-wins for this type, whichever way
            # (enabled or disabled) an earlier, higher-priority root already
            # decided it.
            if schema.type in by_type or schema.type in suppressed:
                continue
            if schema.disabled:
                suppressed.add(schema.type)
                continue
            by_type[schema.type] = schema
    if not by_type:
        searched = ", ".join(str(path) for path, _ in roots) or "(no roots found)"
        raise SchemaLoadError(f"No entity schemas found in any of: {searched}")
    return by_type


@lru_cache(maxsize=32)
def _load_root(directory: Path) -> dict[str, EntitySchema]:
    """One root's own type files, keyed by type. Duplicate types *within*
    this root are an authoring error; duplicates *across* roots are the
    override mechanism and are resolved by the caller (first root wins)."""
    schemas: dict[str, EntitySchema] = {}
    for path in sorted(directory.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise SchemaLoadError(f"{path.name}: invalid yaml: {exc}") from exc
        if not isinstance(data, dict):
            raise SchemaLoadError(f"{path.name}: expected a mapping at top level")
        try:
            schema = EntitySchema.model_validate(data)
        except ValidationError as exc:
            raise SchemaLoadError(f"{path.name}: {exc}") from exc
        if schema.type in schemas:
            raise SchemaLoadError(f"{path.name}: duplicate schema for type '{schema.type}'")
        schemas[schema.type] = schema
    return schemas


# --------------------------------------------------------------------------
# Page-shape templates: narrative body structure, resolved independently of
# field shape so a kb-local override can change just the shape a type uses
# (or just a shape's own template prose) without forking that type's whole
# field list — the same "don't force one override to duplicate the other
# axis" reasoning that keeps skills and entity fields on separate override
# units. Same three-tier precedence (kb-local -> user -> built-in) as entity
# schemas, minus the WAKIL_SCHEMA_PATH override tier — not needed yet; add it
# if a real need shows up.

DEFAULT_TEMPLATE_DIR = Path(__file__).parent / "templates"


def _template_roots(kb_root: Path | None) -> list[SchemaRoot]:
    raw_entries: list[tuple[Path, str]] = []
    if kb_root is not None:
        raw_entries.append((kb_root / "schema" / "templates", SOURCE_KB_LOCAL))
    raw_entries.append((config_home() / "schema" / "templates", SOURCE_USER))
    raw_entries.append((DEFAULT_TEMPLATE_DIR, SOURCE_BUILTIN))

    seen: set[Path] = set()
    roots: list[SchemaRoot] = []
    for raw_path, source in raw_entries:
        normalized = _normalize_root(raw_path)
        if normalized in seen:
            continue
        seen.add(normalized)
        if normalized.is_dir():
            roots.append(SchemaRoot(path=normalized, source=source))
    return roots


def resolve_page_shape_template(shape: str, kb_root: Path | None = None) -> tuple[str, SchemaRoot]:
    """The page-shape template body plus which root won it (whole-file,
    first match wins — same invariant as entity schemas and skills)."""
    for root in _template_roots(kb_root):
        candidate = root.path / f"{shape}.md"
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8"), root
    raise SchemaLoadError(
        f"No page-shape template named {shape!r} found in any of: "
        f"{', '.join(str(r.path) for r in _template_roots(kb_root)) or '(no roots found)'}"
    )
