"""Parse `[[wikilink]]` targets from Markdown text.

Pure, dependency-free — safe to import from indexing (`workspace_service`)
and ingest (`ingest_service`). Uses the same regex shape as
`ingest_service._reconcile_entity_links` so both paths agree on what
counts as a wikilink.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Wikilink form per note-conformance/SKILL.md: [[path]] or [[path|display]].
# Kept in sync with `_WIKILINK_RE` in `ingest_service`.
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


@dataclass(frozen=True)
class Wikilink:
    """A single `[[target]]` or `[[target|display]]` occurrence."""

    target: str
    display: str | None


def parse_wikilinks(text: str) -> list[Wikilink]:
    """Return every wikilink in `text`, in source order, duplicates preserved."""
    return [
        Wikilink(target=match.group(1).strip(), display=match.group(2))
        for match in WIKILINK_RE.finditer(text)
    ]


def normalize_target(target: str) -> str:
    """Comparable key for a wikilink target.

    This kb mixes `[[people/x]]` and `[[sources/y.md]]` conventions for
    genuinely valid links — the `.md`-less form and `Note.path` (always
    `.md`, matching what's on disk) refer to the identical page. Normalize
    both to the same key when resolving.
    """
    stripped = target.strip()
    return stripped[:-3] if stripped.endswith(".md") else stripped
