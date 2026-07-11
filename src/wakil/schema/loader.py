"""Load entity schemas from schema/entities/*.yaml into typed models.

The yaml files ship inside the wakil package (extension happens by forking
and editing them, not by runtime discovery from a workspace — a decision
settled in docs/ingestion-refactor-spec.md). Loading is cached per directory
path, so repeated validation during one command parses the files once.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

DEFAULT_SCHEMA_DIR = Path(__file__).parent / "entities"

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
    directory: str | None = None  # canonical directory, None = no single home
    category: Category
    fields: dict[str, FieldSpec]
    # Per-origin additive sub-schemas (source only), keyed by the base
    # `origin` enum value.
    origins: dict[str, dict[str, FieldSpec]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_category_conventions(self) -> "EntitySchema":
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


def load_entity_schemas(schema_dir: Path | None = None) -> dict[str, EntitySchema]:
    """All entity schemas keyed by `type`, cached per directory."""
    return _load_cached(str((schema_dir or DEFAULT_SCHEMA_DIR).resolve()))


@lru_cache(maxsize=8)
def _load_cached(schema_dir: str) -> dict[str, EntitySchema]:
    directory = Path(schema_dir)
    if not directory.is_dir():
        raise SchemaLoadError(f"Entity schema directory not found: {directory}")
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
    if not schemas:
        raise SchemaLoadError(f"No entity schemas found in {directory}")
    return schemas
