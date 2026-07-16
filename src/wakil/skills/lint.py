"""Deterministic content-quality lint for the built-in skill catalog.

`resolver.py` already validates *structure*: the frontmatter parses, `name`
matches the directory, `skill_api` is supported. This module extends that
with *content-quality* checks that a structurally valid `SKILL.md` can still
fail — a body that's grown too long, a description written in the wrong
voice, a stale cross-reference to a renamed sibling skill, a support file
nobody points to. Every check here is a pure function over text already on
disk: zero model calls, zero network access, safe to run on every commit.

Checks:

1. Line count — a `SKILL.md` body over 500 lines.
2. Reference TOC — a `references/*.md` file over 100 lines with no
   contents-style heading+list near the top.
3. Description shape — non-empty, under 1024 chars, not first/second-person.
4. Time-sensitive phrasing — "as of this writing" / "as of now" / "as of
   today", anywhere in the body.
5. Dangling cross-references — a backtick-quoted, catalog-shaped name that
   isn't actually one of the live built-in skills.
6. Orphaned support files — a `references/`/`templates/` file no line of
   `SKILL.md` links to or mentions by its relative path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import frontmatter

from wakil.skills.models import ResolutionContext
from wakil.skills.resolver import SKILL_NAME_RE, discover_skill_names

if TYPE_CHECKING:
    from collections.abc import Iterable

    from wakil.skills.models import ResolvedSkill

MAX_SKILL_BODY_LINES = 500
REFERENCE_TOC_LINE_THRESHOLD = 100
TOC_HEADING_SCAN_LINES = 20
TOC_LIST_LOOKAHEAD_LINES = 5
MAX_DESCRIPTION_LENGTH = 1024
TIME_SENSITIVE_PHRASES = ("as of this writing", "as of now", "as of today")
SUPPORT_SUBDIRS = ("references", "templates")

# Only whole backtick-quoted spans are considered candidate skill-name
# references. Scanning bare (non-backtick) prose for kebab-case words was
# tried and rejected: ordinary English compounds ("code-owned", "re-read",
# "long-document") vastly outnumber real skill references and would swamp
# this check with noise — exactly the over-engineering the brief warns
# against for a 12-skill catalog.
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
_TOC_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S")


@dataclass(frozen=True)
class LintFinding:
    """One content-quality finding for one skill."""

    skill: str
    check: str
    message: str


def builtin_catalog_names(builtin_root: Path) -> list[str]:
    """The live built-in catalog's skill names, scoped to `builtin_root` only.

    Used as the source of truth for the dangling-cross-reference check, via
    `discover_skill_names` rather than a hardcoded list — a hardcoded list
    is exactly the staleness risk this check exists to catch. The kb-local
    and user roots are pointed at paths that don't exist so they're silently
    dropped (per `resolve_roots`) and never leak a kb-local/user override's
    name into what counts as "resolvable" here.
    """
    context = ResolutionContext(
        kb_root=builtin_root / ".lint-no-kb-root",
        user_skill_root=builtin_root / ".lint-no-user-root",
        builtin_skill_root=builtin_root,
    )
    return discover_skill_names(context)


def lint_skill(resolved: ResolvedSkill, catalog_names: Iterable[str]) -> list[LintFinding]:
    """Run every content-quality check against one resolved skill.

    `catalog_names` is the universe of names the dangling-cross-reference
    check treats as resolvable — pass `builtin_catalog_names(...)`.
    """
    catalog = set(catalog_names)
    text = resolved.manifest.read_text(encoding="utf-8")
    post = frontmatter.loads(text)
    body = post.content
    description = post.metadata.get("description") or ""
    if not isinstance(description, str):
        description = str(description)

    findings: list[LintFinding] = []
    findings += _check_line_count(resolved.name, body)
    findings += _check_description_shape(resolved.name, description)
    findings += _check_time_sensitive_phrasing(resolved.name, body)
    findings += _check_dangling_cross_references(resolved.name, body, catalog)
    findings += _check_reference_toc(resolved.name, resolved.directory)
    findings += _check_orphaned_support_files(resolved.name, resolved.directory, body)
    return findings


def _check_line_count(skill: str, body: str) -> list[LintFinding]:
    line_count = len(body.splitlines())
    if line_count <= MAX_SKILL_BODY_LINES:
        return []
    return [
        LintFinding(
            skill,
            "line-count",
            f"SKILL.md body is {line_count} lines, over the {MAX_SKILL_BODY_LINES}-line "
            "ceiling — split it into references/ or trim it.",
        )
    ]


def _check_description_shape(skill: str, description: str) -> list[LintFinding]:
    findings: list[LintFinding] = []
    if not description.strip():
        findings.append(
            LintFinding(skill, "description-shape", "frontmatter description is empty.")
        )
        return findings
    if len(description) > MAX_DESCRIPTION_LENGTH:
        findings.append(
            LintFinding(
                skill,
                "description-shape",
                f"description is {len(description)} chars, over the "
                f"{MAX_DESCRIPTION_LENGTH}-char limit.",
            )
        )
    stripped = description.lstrip()
    if stripped.startswith("I ") or stripped.startswith("You "):
        findings.append(
            LintFinding(
                skill,
                "description-shape",
                "description starts with first/second-person phrasing "
                f"({stripped.split()[0]!r}) — describe what the skill does, not "
                "who is doing it.",
            )
        )
    return findings


def _check_time_sensitive_phrasing(skill: str, body: str) -> list[LintFinding]:
    findings: list[LintFinding] = []
    lower = body.lower()
    for phrase in TIME_SENSITIVE_PHRASES:
        idx = lower.find(phrase)
        if idx == -1:
            continue
        line_no = body.count("\n", 0, idx) + 1
        findings.append(
            LintFinding(
                skill,
                "time-sensitive-phrasing",
                f"line {line_no}: {phrase!r} reads as time-sensitive and will go stale.",
            )
        )
    return findings


def _adjacent_to_arrow(text: str, start: int, end: int) -> bool:
    """True if a `→` sits right before or after the span, ignoring whitespace.

    Excludes inline rename-pair lists (`` `end_date`→`end-date` ``) from the
    cross-reference check — those are frontmatter field names, not skill
    references, even though they happen to be valid kebab-case.
    """
    before = text[:start].rstrip()
    after = text[end:].lstrip()
    return before.endswith("→") or after.startswith("→")


def _check_dangling_cross_references(
    skill: str, body: str, catalog_names: set[str]
) -> list[LintFinding]:
    findings: list[LintFinding] = []
    seen: set[str] = set()
    for match in _BACKTICK_RE.finditer(body):
        token = match.group(1)
        if "-" not in token or not SKILL_NAME_RE.match(token):
            continue
        if token in catalog_names or token in seen:
            continue
        if _adjacent_to_arrow(body, match.start(), match.end()):
            continue
        seen.add(token)
        findings.append(
            LintFinding(
                skill,
                "dangling-cross-reference",
                f"`{token}` looks like a catalog skill name but is not in the live "
                f"builtin catalog ({', '.join(sorted(catalog_names))}).",
            )
        )
    return findings


def _has_toc(lines: list[str]) -> bool:
    scan = lines[:TOC_HEADING_SCAN_LINES]
    for i, line in enumerate(scan):
        heading = _TOC_HEADING_RE.match(line)
        if not heading:
            continue
        heading_text = heading.group(2).lower()
        if "content" not in heading_text and "toc" not in heading_text:
            continue
        lookahead = scan[i + 1 : i + 1 + TOC_LIST_LOOKAHEAD_LINES]
        if any(_LIST_ITEM_RE.match(candidate) for candidate in lookahead):
            return True
    return False


def _check_reference_toc(skill: str, directory: Path) -> list[LintFinding]:
    references_dir = directory / "references"
    if not references_dir.is_dir():
        return []
    findings: list[LintFinding] = []
    for path in sorted(references_dir.rglob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) <= REFERENCE_TOC_LINE_THRESHOLD:
            continue
        if _has_toc(lines):
            continue
        rel = path.relative_to(directory).as_posix()
        findings.append(
            LintFinding(
                skill,
                "reference-toc",
                f"{rel} is {len(lines)} lines with no '## Contents'-style heading "
                "and list near the top.",
            )
        )
    return findings


def _check_orphaned_support_files(skill: str, directory: Path, body: str) -> list[LintFinding]:
    findings: list[LintFinding] = []
    for subdir_name in SUPPORT_SUBDIRS:
        subdir = directory / subdir_name
        if not subdir.is_dir():
            continue
        for path in sorted(subdir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(directory).as_posix()
            if rel in body:
                continue
            findings.append(
                LintFinding(
                    skill,
                    "orphaned-support-file",
                    f"{rel} isn't linked or mentioned by relative path in SKILL.md.",
                )
            )
    return findings
