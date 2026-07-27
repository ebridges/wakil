"""Validate on-disk frontmatter against the entity schemas.

`wakil enrich`'s DAG already gates every write through `validate_proposal` /
`validate_frontmatter` (`ingest_service.py`) before anything reaches disk.
The prompt-only skills (entity-enrichment, note-routing, note-conformance,
content-synthesis) have no equivalent Python-level check — a skill-driven
manual write can put an invalid value (a bad enum, a missing required field)
straight onto disk. `wakil schema validate` runs the exact same
`validate_frontmatter` check against already-written files, as a real
conformance check to pair with `wakil schema migrate`'s mechanical-only
fixes (see note-conformance/SKILL.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from glob import glob
from pathlib import Path

import frontmatter as frontmatter_lib

from wakil.schema.validate import SchemaError, validate_frontmatter


@dataclass
class FileValidation:
    path: Path
    errors: list[SchemaError] = field(default_factory=list)
    # Set instead of `errors` when the file couldn't even be checked against
    # a schema (unreadable, unparsable frontmatter, no `type:` field).
    load_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.load_error is None and not self.errors


def collect_markdown_files(paths: list[str]) -> list[Path]:
    """Expand file/directory/glob arguments into a sorted, de-duplicated list.

    A directory argument is walked recursively for `*.md` files; an existing
    file argument is taken as-is; anything else is treated as a glob pattern
    (so `notes/*.md` works without the shell already having expanded it).
    """
    files: set[Path] = set()
    for raw in paths:
        candidate = Path(raw)
        if candidate.is_dir():
            files.update(candidate.rglob("*.md"))
        elif candidate.is_file():
            files.add(candidate)
        else:
            files.update(Path(match) for match in glob(raw, recursive=True))
    return sorted(files)


def validate_file(path: Path, kb_root: Path | None) -> FileValidation:
    """Parse one file's frontmatter and check it against its declared type's schema."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return FileValidation(path=path, load_error=f"unreadable ({exc})")

    try:
        post = frontmatter_lib.loads(content)
    except Exception as exc:  # python-frontmatter raises on malformed YAML
        return FileValidation(path=path, load_error=f"invalid frontmatter ({exc})")

    metadata = post.metadata
    entity_type = metadata.get("type") if isinstance(metadata, dict) else None
    if not isinstance(entity_type, str) or not entity_type:
        return FileValidation(path=path, load_error="no `type:` frontmatter")

    errors = validate_frontmatter(entity_type, metadata, kb_root)
    return FileValidation(path=path, errors=errors)
