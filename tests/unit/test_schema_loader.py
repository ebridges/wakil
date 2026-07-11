"""Phase A tests: entity schema yaml parsing + frontmatter validation
(required/forbidden field checks, enum/kind validation) per the refactor
spec's testing strategy."""

import datetime as dt
from pathlib import Path

import pytest

from wakil.schema.loader import (
    DEFAULT_SCHEMA_DIR,
    SchemaLoadError,
    load_entity_schemas,
)
from wakil.schema.validate import known_types, validate_frontmatter

EXPECTED_TYPES = {
    "person",
    "company",
    "project",
    "concept",
    "meeting",
    "journal",
    "assessment",
    "reflection",
    "idea",
    "organization",
    "meta",
    "index",
    "source",
}


def _valid_person() -> dict:
    return {
        "type": "person",
        "name": "Jane Doe",
        "status": "active",
        "company": "acme",
        "tags": ["claims"],
        "created": "2026-07-10",
        "updated": "2026-07-10",
    }


# ---------------------------------------------------------------------------
# Loader


def test_shipped_schemas_load_and_cover_expected_types():
    schemas = load_entity_schemas()
    assert set(schemas) == EXPECTED_TYPES


def test_loader_is_cached():
    assert load_entity_schemas() is load_entity_schemas()
    assert load_entity_schemas(DEFAULT_SCHEMA_DIR) is load_entity_schemas()


def test_category_split_matches_entity_metadata_doc():
    schemas = load_entity_schemas()
    assert schemas["person"].category == "identity"
    assert schemas["company"].category == "identity"
    for doc_type in ("meeting", "source", "reflection", "journal", "meta", "index", "assessment"):
        assert schemas[doc_type].category == "document", doc_type
    for hybrid in ("concept", "project", "organization", "idea"):
        assert schemas[hybrid].category == "hybrid", hybrid


def test_source_schema_has_origin_sub_schemas():
    source = load_entity_schemas()["source"]
    assert set(source.origins) <= set(source.fields["origin"].values)
    assert "readwise_id" in source.origins["export"]


def test_loader_rejects_malformed_yaml(tmp_path: Path):
    (tmp_path / "bad.yaml").write_text(": not [valid yaml")
    with pytest.raises(SchemaLoadError, match="invalid yaml"):
        load_entity_schemas(tmp_path)


def test_loader_rejects_missing_directory(tmp_path: Path):
    with pytest.raises(SchemaLoadError, match="not found"):
        load_entity_schemas(tmp_path / "nope")


def test_loader_rejects_schema_contradicting_its_category(tmp_path: Path):
    (tmp_path / "broken.yaml").write_text(
        "type: broken\ndirectory: x\ncategory: identity\n"
        "fields:\n  name: {required: true, kind: string}\n"
        "  title: {required: false, kind: string}\n"
    )
    with pytest.raises(SchemaLoadError, match="must not define `title`"):
        load_entity_schemas(tmp_path)


def test_loader_rejects_enum_without_values(tmp_path: Path):
    (tmp_path / "broken.yaml").write_text(
        "type: broken\ndirectory: x\ncategory: document\n"
        "fields:\n  title: {required: true, kind: string}\n"
        "  status: {required: false, kind: enum}\n"
    )
    with pytest.raises(SchemaLoadError, match="values"):
        load_entity_schemas(tmp_path)


def test_loader_rejects_duplicate_types(tmp_path: Path):
    body = (
        "type: dup\ndirectory: x\ncategory: document\n"
        "fields:\n  title: {required: true, kind: string}\n"
    )
    (tmp_path / "a.yaml").write_text(body)
    (tmp_path / "b.yaml").write_text(body)
    with pytest.raises(SchemaLoadError, match="duplicate"):
        load_entity_schemas(tmp_path)


# ---------------------------------------------------------------------------
# Validation


def test_valid_person_passes():
    assert validate_frontmatter("person", _valid_person()) == []


def test_unknown_type_is_an_error_not_a_guess():
    errors = validate_frontmatter("learning-agenda", {"name": "x"})
    assert len(errors) == 1
    assert "no entity schema defines type 'learning-agenda'" in errors[0].message
    assert "concept" in errors[0].message  # lists the known types


def test_known_types_helper():
    assert known_types() == sorted(EXPECTED_TYPES)


def test_missing_required_fields_reported():
    errors = validate_frontmatter("person", {"type": "person", "name": "Jane Doe"})
    missing = {e.field for e in errors}
    assert missing == {"status", "created", "updated"}
    assert all("required" in e.message for e in errors)


def test_required_field_empty_string_counts_as_missing():
    person = _valid_person() | {"status": ""}
    errors = validate_frontmatter("person", person)
    assert [e.field for e in errors] == ["status"]


def test_enum_violation_reported():
    person = _valid_person() | {"status": "bogus"}
    errors = validate_frontmatter("person", person)
    assert len(errors) == 1
    assert errors[0].field == "status"
    assert "active" in errors[0].message


def test_identity_type_forbids_title():
    person = _valid_person() | {"title": "Jane Doe"}
    errors = validate_frontmatter("person", person)
    assert [e.field for e in errors] == ["title"]
    assert "identity" in errors[0].message


def test_document_type_forbids_name():
    meeting = {
        "type": "meeting",
        "title": "Claims Kickoff",
        "name": "Claims Kickoff",
        "date": "2026-07-09",
        "created": "2026-07-10",
    }
    errors = validate_frontmatter("meeting", meeting)
    assert [e.field for e in errors] == ["name"]


def test_hybrid_type_allows_both_name_and_title():
    concept = {
        "type": "concept",
        "name": "Graph Memory",
        "title": "Graph Memory: An Overview",
        "created": "2026-07-10",
        "updated": "2026-07-10",
    }
    assert validate_frontmatter("concept", concept) == []


def test_type_mismatch_reported():
    errors = validate_frontmatter("person", _valid_person() | {"type": "concept"})
    assert any(e.field == "type" and "expected 'person'" in e.message for e in errors)


def test_kind_checks_date_list_bool_int():
    journal = {
        "type": "journal",
        "date": "not-a-date",
        "week": "2026-W28",
        "day": "mon",
        "year": "twenty-six",
        "title": "A day",
        "topics": "should-be-a-list",
        "tags": [],
        "created": dt.date(2026, 7, 10),  # yaml-parsed date objects are fine
    }
    errors = {e.field: e.message for e in validate_frontmatter("journal", journal)}
    assert "ISO date" in errors["date"]
    assert "integer" in errors["year"]
    assert "list" in errors["topics"]
    assert "created" not in errors

    assessment = {
        "type": "assessment",
        "subject": "self",
        "sensitive": "yes",
        "created": "2026-07-10",
    }
    errors = {e.field: e.message for e in validate_frontmatter("assessment", assessment)}
    assert "boolean" in errors["sensitive"]


def test_unknown_extra_fields_are_tolerated():
    # entity-metadata.md rec 5: low-n fields stay free-form extensions.
    person = _valid_person() | {"linear_id": "ABC-123", "team": "claims"}
    assert validate_frontmatter("person", person) == []


def test_source_origin_sub_schema_fields_are_kind_checked():
    source = {
        "type": "source",
        "title": "How Graph Memory Helps",
        "origin": "article",
        "captured": "2026-07-10",
        "published": "not-a-date",
    }
    errors = validate_frontmatter("source", source)
    assert [e.field for e in errors] == ["published"]

    # The same field is unconstrained for an origin whose sub-schema
    # doesn't declare it (extras are tolerated).
    source_manual = source | {"origin": "manual"}
    assert validate_frontmatter("source", source_manual) == []


def test_source_requires_captured_not_retrieved():
    source = {
        "type": "source",
        "title": "A capture",
        "origin": "manual",
        "retrieved": "2026-07-10",  # wakil's old, wrong field name
    }
    errors = validate_frontmatter("source", source)
    assert [e.field for e in errors] == ["captured"]
