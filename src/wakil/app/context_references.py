"""Expansion of @file:/@url: references inside --context/--context-file values."""

import mimetypes
import re
from pathlib import Path

from wakil.integrations.web import FetchError, fetch_article
from wakil.knowledge.markdown import slice_heading_section

CHARS_PER_TOKEN = 4
MODEL_CONTEXT_WINDOW_TOKENS = 180_000
WARN_BUDGET_FRACTION = 0.25
HARD_BUDGET_FRACTION = 0.50

_LANGUAGE_BY_SUFFIX = {
    ".md": "markdown",
    ".py": "python",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".txt": "",
    ".sh": "bash",
}

_REF_RE = re.compile(
    r'(?<![\w/])@(?:(?P<kind>file|url):(?:"(?P<qval>[^"]+)"|(?P<val>\S+))|(?P<bare>\S+))'
)
_RANGE_RE = re.compile(r":(\d+)-(\d+)$")


class ContextResolutionError(RuntimeError):
    pass


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def expand_piece(raw_text: str, workspace_root: Path) -> str:
    blocks: list[str] = []

    def _replace(match: re.Match) -> str:
        label = match.group(0)
        kind = match.group("kind")
        if kind == "file":
            value = match.group("qval") or match.group("val")
            path_str, line_start, line_end, heading = _parse_ref_value(value)
            blocks.append(
                _resolve_file(path_str, line_start, line_end, heading, workspace_root, label)
            )
            return ""
        if kind == "url":
            value = match.group("qval") or match.group("val")
            blocks.append(_resolve_url(value, label))
            return ""
        bare = match.group("bare")
        path_str, line_start, line_end, heading = _parse_ref_value(bare)
        if not _looks_like_file_reference(path_str):
            return label
        blocks.append(
            _resolve_file(path_str, line_start, line_end, heading, workspace_root, label)
        )
        return ""

    stripped = _REF_RE.sub(_replace, raw_text)
    if not blocks:
        return raw_text
    stripped = re.sub(r"[ \t]{2,}", " ", stripped).strip()
    attached = "\n\n--- Attached Context ---\n\n" + "\n\n".join(blocks)
    return stripped + attached


def resolve_context(
    *, context: list[str], context_files: list[Path], workspace_root: Path
) -> tuple[str | None, list[str]]:
    if not context and not context_files:
        return None, []

    pieces: list[str] = []
    for value in context:
        pieces.append(expand_piece(value, workspace_root))
    for file_path in context_files:
        try:
            raw = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ContextResolutionError(f"Could not read context file {file_path}: {exc}") from exc
        pieces.append(expand_piece(raw, workspace_root))

    text = "\n\n---\n\n".join(pieces)
    tokens = estimate_tokens(text)
    hard_budget = int(MODEL_CONTEXT_WINDOW_TOKENS * HARD_BUDGET_FRACTION)
    warn_budget = int(MODEL_CONTEXT_WINDOW_TOKENS * WARN_BUDGET_FRACTION)

    if tokens > hard_budget:
        raise ContextResolutionError(
            f"Context is too large ({tokens} tokens, over the {hard_budget}-token hard budget)"
        )
    if tokens > warn_budget:
        warning = f"Context is large ({tokens} tokens, over the {warn_budget}-token soft budget)"
        return text, [warning]
    return text, []


def _looks_like_file_reference(value: str) -> bool:
    return "/" in value or Path(value).suffix.lower() in _LANGUAGE_BY_SUFFIX


def _parse_ref_value(raw: str) -> tuple[str, int | None, int | None, str | None]:
    if "#" in raw:
        path_str, _, heading = raw.rpartition("#")
        return path_str, None, None, heading
    match = _RANGE_RE.search(raw)
    if match:
        path_str = raw[: match.start()]
        return path_str, int(match.group(1)), int(match.group(2)), None
    return raw, None, None, None


def _is_binary(path: Path) -> bool:
    if path.suffix.lower() in _LANGUAGE_BY_SUFFIX:
        return False
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type is not None:
        return not mime_type.startswith("text/")
    with path.open("rb") as handle:
        chunk = handle.read(4096)
    return b"\x00" in chunk


def _resolve_workspace_path(path_str: str, workspace_root: Path) -> Path:
    path = Path(path_str)
    if not path.is_absolute():
        path = workspace_root / path
    resolved = path.resolve()
    try:
        resolved.relative_to(workspace_root.resolve())
    except ValueError:
        raise ContextResolutionError(f"{path_str} is outside the workspace") from None
    return resolved


def _resolve_file(
    path_str: str,
    line_start: int | None,
    line_end: int | None,
    heading: str | None,
    workspace_root: Path,
    label: str,
) -> str:
    resolved = _resolve_workspace_path(path_str, workspace_root)
    if not resolved.is_file():
        raise ContextResolutionError(f"File not found: {path_str}")
    if _is_binary(resolved):
        raise ContextResolutionError(f"Cannot include binary file: {path_str}")

    text = resolved.read_text(encoding="utf-8", errors="replace")
    if heading is not None:
        sliced = slice_heading_section(text, heading)
        if sliced is None:
            raise ContextResolutionError(f"Heading {heading!r} not found in {path_str}")
    elif line_start is not None and line_end is not None:
        lines = text.splitlines()
        count = len(lines)
        start = min(max(line_start, 1), count) if count else 1
        end = min(max(line_end, start), count) if count else 1
        sliced = "\n".join(lines[start - 1 : end])
    else:
        sliced = text

    lang = _LANGUAGE_BY_SUFFIX.get(resolved.suffix.lower(), "")
    tokens = estimate_tokens(sliced)
    return f"📄 {label} ({tokens} tokens)\n```{lang}\n{sliced}\n```"


def _resolve_url(url: str, label: str) -> str:
    try:
        article = fetch_article(url)
    except FetchError as exc:
        raise ContextResolutionError(f"Could not fetch {url}: {exc}") from exc
    tokens = estimate_tokens(article.text)
    return f"🌐 {label} ({tokens} tokens)\n```\n{article.text}\n```"
