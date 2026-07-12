"""Frontmatter validation against the entity schemas.

Applies to NEW writes only — reading and indexing existing files stays as
tolerant as `Note.frontmatter_json` is today (docs/ingestion-refactor-spec.md).
Unknown extra fields are tolerated (entity-metadata.md recommendation 5:
low-n fields stay free-form extensions); only the category-level name/title
rules produce forbidden-field errors.
"""

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

from wakil.schema.loader import EntitySchema, FieldSpec, load_entity_schemas

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class SchemaError:
    field: str  # "" for whole-document errors
    message: str

    def __str__(self) -> str:
        return f"{self.field}: {self.message}" if self.field else self.message


def known_types(schema_dir: Path | None = None) -> list[str]:
    return sorted(load_entity_schemas(schema_dir))


def validate_frontmatter(
    entity_type: str, frontmatter: dict, schema_dir: Path | None = None
) -> list[SchemaError]:
    """Validate a new page's frontmatter; empty list means valid.

    An unknown entity type is itself an error — the hard-stop consumers
    (validate_proposal, the migration tool) rely on this rather than
    best-guessing a schema.
    """
    schemas = load_entity_schemas(schema_dir)
    schema = schemas.get(entity_type)
    if schema is None:
        return [
            SchemaError(
                field="type",
                message=(
                    f"no entity schema defines type '{entity_type}' "
                    f"(known: {', '.join(sorted(schemas))})"
                ),
            )
        ]

    errors: list[SchemaError] = []
    declared = frontmatter.get("type")
    if declared is not None and declared != entity_type:
        errors.append(
            SchemaError(field="type", message=f"declares '{declared}', expected '{entity_type}'")
        )

    # Category-level name/title rules (the identity/document/hybrid split).
    if schema.category == "identity" and _present(frontmatter.get("title")):
        errors.append(
            SchemaError(field="title", message="identity types use `name` only, not `title`")
        )
    if schema.category == "document" and _present(frontmatter.get("name")):
        errors.append(
            SchemaError(field="name", message="document types use `title` only, not `name`")
        )

    effective_fields = dict(schema.fields)
    origin = frontmatter.get("origin")
    if schema.origins and isinstance(origin, str):
        effective_fields.update(schema.origins.get(origin, {}))

    for field_name, spec in effective_fields.items():
        value = frontmatter.get(field_name)
        if not _present(value):
            if spec.required:
                errors.append(SchemaError(field=field_name, message="required field is missing"))
            continue
        error = _check_kind(field_name, value, spec)
        if error is not None:
            errors.append(error)

    return errors


def _present(value) -> bool:
    """Missing, None, and empty-string placeholders all count as absent."""
    if value is None:
        return False
    return not (isinstance(value, str) and not value.strip())


def _check_kind(field_name: str, value, spec: FieldSpec) -> SchemaError | None:
    if spec.kind == "string":
        if not isinstance(value, str):
            return SchemaError(field_name, f"expected a string, got {type(value).__name__}")
    elif spec.kind == "list":
        if not isinstance(value, list):
            return SchemaError(field_name, f"expected a list, got {type(value).__name__}")
    elif spec.kind == "enum":
        if value not in (spec.values or []):
            return SchemaError(
                field_name, f"'{value}' is not one of: {', '.join(spec.values or [])}"
            )
    elif spec.kind == "date":
        if not _is_date(value):
            return SchemaError(field_name, f"expected an ISO date (YYYY-MM-DD), got {value!r}")
    elif spec.kind == "bool":
        if not isinstance(value, bool):
            return SchemaError(field_name, f"expected a boolean, got {type(value).__name__}")
    elif spec.kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return SchemaError(field_name, f"expected an integer, got {type(value).__name__}")
    elif spec.kind == "ref" and not isinstance(value, str):
        # A ref is a wikilink/slug string; resolution is the resolver's job,
        # not the schema's.
        return SchemaError(field_name, f"expected a reference string, got {type(value).__name__}")
    return None


def _is_date(value) -> bool:
    if isinstance(value, dt.datetime):
        return True
    if isinstance(value, dt.date):
        return True
    return isinstance(value, str) and bool(_ISO_DATE_RE.match(value.strip()))


def schema_for(entity_type: str, schema_dir: Path | None = None) -> EntitySchema | None:
    return load_entity_schemas(schema_dir).get(entity_type)
