"""Skill root resolution, discovery, and validation.

Implements the search-path algorithm from `docs/skill-resolution-specification.md`:
an ordered list of skill roots (an explicit `WAKIL_SKILL_PATH` override, the
knowledge-base-local `skills/` directory, the user-level config directory, and
the built-in root) is normalized, and the first matching, valid skill
directory wins.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

import frontmatter
from pydantic import ValidationError

from wakil.config.registry import config_home
from wakil.skills.errors import SkillResolutionError
from wakil.skills.models import (
    SKILL_API_VERSION,
    SOURCE_BUILTIN,
    SOURCE_KB_LOCAL,
    SOURCE_OVERRIDE,
    SOURCE_USER,
    ResolutionContext,
    ResolvedSkill,
    RootIssue,
    RootResolution,
    SkillMetadata,
    SkillRoot,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")


def validate_skill_name(name: str) -> None:
    """Raise if `name` is not a valid lowercase-kebab-case skill name.

    Rejecting anything that doesn't match `SKILL_NAME_RE` also blocks path
    traversal: `/`, `..`, `.`, uppercase letters, and whitespace can't match.
    """
    if not SKILL_NAME_RE.match(name):
        raise SkillResolutionError(
            reason="invalid_name",
            message=f"{name!r} is not a valid skill name (expected lowercase kebab-case).",
            name=name,
        )


def parse_skill_path(value: str | None) -> list[Path]:
    """Split a `WAKIL_SKILL_PATH`-style value on the platform path separator.

    Empty segments are dropped so they can never be mistaken for the current
    working directory. Paths are returned un-normalized; normalization
    happens in `resolve_roots`.
    """
    if not value:
        return []
    return [Path(segment) for segment in value.split(os.pathsep) if segment]


def _normalize_root(raw: Path) -> Path:
    expanded = os.path.expandvars(str(raw))
    return Path(expanded).expanduser().resolve()


def resolve_roots(context: ResolutionContext) -> RootResolution:
    """Build the ordered, normalized, deduplicated list of usable skill roots.

    Never raises. Problems with explicit (`WAKIL_SKILL_PATH`) roots are
    reported via `RootResolution.issues`; missing or non-directory default
    roots are silently dropped per spec §7.
    """
    raw_entries: list[tuple[Path, str, bool]] = []
    for path in parse_skill_path(context.skill_path):
        raw_entries.append((path, SOURCE_OVERRIDE, True))
    raw_entries.append((context.kb_root / "skills", SOURCE_KB_LOCAL, False))
    raw_entries.append((context.user_skill_root, SOURCE_USER, False))
    raw_entries.append((context.builtin_skill_root, SOURCE_BUILTIN, False))

    seen: set[Path] = set()
    unique_entries: list[tuple[Path, str, bool]] = []
    for raw_path, source, explicit in raw_entries:
        normalized = _normalize_root(raw_path)
        if normalized in seen:
            continue
        seen.add(normalized)
        unique_entries.append((normalized, source, explicit))

    roots: list[SkillRoot] = []
    issues: list[RootIssue] = []
    for normalized, source, explicit in unique_entries:
        if not normalized.exists():
            if explicit:
                issues.append(
                    RootIssue(
                        root=normalized,
                        source=source,
                        reason="missing",
                        message=f"Skill root {normalized} does not exist.",
                    )
                )
            continue
        if not normalized.is_dir():
            if explicit:
                issues.append(
                    RootIssue(
                        root=normalized,
                        source=source,
                        reason="not_a_directory",
                        message=f"Skill root {normalized} exists but is not a directory.",
                    )
                )
            continue
        roots.append(SkillRoot(path=normalized, source=source))

    return RootResolution(roots=roots, issues=issues)


def _load_skill(skill_dir: Path, expected_name: str) -> tuple[Path, SkillMetadata]:
    if not skill_dir.is_dir():
        raise SkillResolutionError(
            reason="invalid_directory",
            message=f"{skill_dir} is not a directory.",
            name=expected_name,
            path=skill_dir,
        )

    manifest = skill_dir / "SKILL.md"
    if not manifest.is_file():
        raise SkillResolutionError(
            reason="invalid_directory",
            message=f"{manifest} is missing or is not a readable file.",
            name=expected_name,
            path=manifest,
        )

    try:
        text = manifest.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillResolutionError(
            reason="invalid_directory",
            message=f"{manifest} could not be read: {exc}",
            name=expected_name,
            path=manifest,
        ) from exc

    try:
        post = frontmatter.loads(text)
    except Exception as exc:
        raise SkillResolutionError(
            reason="invalid_metadata",
            message=f"{manifest} has malformed frontmatter: {exc}",
            name=expected_name,
            path=manifest,
        ) from exc

    try:
        metadata = SkillMetadata.model_validate(dict(post.metadata))
    except ValidationError as exc:
        raise SkillResolutionError(
            reason="invalid_metadata",
            message=f"{manifest} has invalid metadata: {exc}",
            name=expected_name,
            path=manifest,
        ) from exc

    if metadata.name != expected_name:
        raise SkillResolutionError(
            reason="invalid_metadata",
            message=(
                f"{manifest} declares name {metadata.name!r}, "
                f"which does not match its directory name {expected_name!r}."
            ),
            name=expected_name,
            path=manifest,
        )

    if metadata.skill_api != SKILL_API_VERSION:
        raise SkillResolutionError(
            reason="unsupported_api",
            message=(
                f"{manifest} declares skill_api={metadata.skill_api}, "
                f"but this wakil only supports skill_api={SKILL_API_VERSION}."
            ),
            name=expected_name,
            path=manifest,
        )

    return manifest, metadata


def resolve_skill(name: str, context: ResolutionContext) -> ResolvedSkill:
    """Resolve `name` to its winning skill directory, validating it in the process.

    The first root containing a `<name>/` directory is authoritative: if it
    is invalid, resolution fails rather than falling back to a lower-precedence
    root (spec §9.2).
    """
    validate_skill_name(name)

    root_resolution = resolve_roots(context)
    for issue in root_resolution.issues:
        if issue.reason == "not_a_directory":
            raise SkillResolutionError(
                reason="invalid_root",
                message=issue.message,
                path=issue.root,
            )

    for root in root_resolution.roots:
        skill_dir = root.path / name
        if not skill_dir.exists():
            continue
        manifest, metadata = _load_skill(skill_dir, expected_name=name)
        return ResolvedSkill(
            name=name,
            source=root.source,
            root=root.path,
            directory=skill_dir,
            manifest=manifest,
            metadata=metadata,
        )

    raise SkillResolutionError(
        reason="not_found",
        message=f"No skill named {name!r} was found in any configured skill root.",
        name=name,
        searched_roots=[root.path for root in root_resolution.roots],
    )


def discover_skill_names(context: ResolutionContext) -> list[str]:
    """Sorted, deduped candidate skill names found as immediate children of any root.

    Does not validate the skills, only enumerates directory names that look
    like skill names.
    """
    names: set[str] = set()
    for root in resolve_roots(context).roots:
        for child in root.path.iterdir():
            if child.is_dir() and SKILL_NAME_RE.match(child.name):
                names.add(child.name)
    return sorted(names)


def find_shadowed_roots(name: str, context: ResolutionContext) -> list[SkillRoot]:
    """Roots (in precedence order) where a `<name>/` subdirectory exists.

    Existence check only, no validation of the candidate directories.
    """
    validate_skill_name(name)
    return [
        root
        for root in resolve_roots(context).roots
        if (root.path / name).exists()
    ]


def default_context(
    kb_root: Path, *, environ: Mapping[str, str] | None = None
) -> ResolutionContext:
    """Build the standard `ResolutionContext` for a knowledge base."""
    env = environ if environ is not None else os.environ
    return ResolutionContext(
        kb_root=kb_root,
        user_skill_root=config_home() / "skills",
        builtin_skill_root=Path(__file__).resolve().parent,
        skill_path=env.get("WAKIL_SKILL_PATH"),
    )
