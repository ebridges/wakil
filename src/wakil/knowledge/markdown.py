"""Markdown file discovery and metadata extraction."""

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

# Directories never indexed as knowledge content.
SKIPPED_DIRS = {".git", ".wakil", ".obsidian", "node_modules", ".venv"}

_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class MarkdownFile:
    """Metadata extracted from one Markdown file in the knowledge base."""

    path: Path  # relative to the workspace root
    title: str
    content_hash: str
    metadata: dict = field(default_factory=dict)


def discover_markdown_files(root: Path) -> list[Path]:
    """Return workspace-relative paths of all indexable Markdown files."""
    results: list[Path] = []
    for path in sorted(root.rglob("*.md")):
        relative = path.relative_to(root)
        if any(part in SKIPPED_DIRS or part.startswith(".") for part in relative.parts[:-1]):
            continue
        results.append(relative)
    return results


def read_markdown_file(root: Path, relative_path: Path) -> MarkdownFile:
    """Parse one Markdown file into indexable metadata.

    Malformed frontmatter must not break indexing: fall back to treating the
    whole file as plain content.
    """
    full_path = root / relative_path
    raw = full_path.read_bytes()
    content_hash = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8", errors="replace")

    try:
        post = frontmatter.loads(text)
        metadata = dict(post.metadata)
        body = post.content
    except Exception:
        metadata = {}
        body = text

    title = _extract_title(metadata, body, relative_path)
    return MarkdownFile(
        path=relative_path, title=title, content_hash=content_hash, metadata=metadata
    )


def _extract_title(metadata: dict, body: str, relative_path: Path) -> str:
    for key in ("title", "name"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    match = _H1_RE.search(body)
    if match:
        return match.group(1)
    return relative_path.stem
