"""Data types for skill resolution: metadata, resolved skills, and roots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

SKILL_API_VERSION = 1

SOURCE_OVERRIDE = "override"
SOURCE_KB_LOCAL = "kb-local"
SOURCE_USER = "user"
SOURCE_BUILTIN = "builtin"


class SkillMetadata(BaseModel):
    """Parsed `SKILL.md` frontmatter."""

    name: str
    skill_api: int
    version: int | None = None


class ResolvedSkill(BaseModel):
    """A successfully resolved skill and where it came from."""

    name: str
    source: str
    root: Path
    directory: Path
    manifest: Path
    metadata: SkillMetadata

    def resource(self, relative: str) -> Path:
        """Path to a supporting file, resolved within this skill's own directory.

        Never against the KB root, current working directory, or another
        skill's implementation. Rejects `..` traversal and absolute-looking
        inputs that would otherwise escape the selected skill directory.
        """
        directory = self.directory
        candidate = (directory / relative).resolve()
        try:
            candidate.relative_to(directory)
        except ValueError as exc:
            from wakil.skills.errors import SkillResolutionError

            raise SkillResolutionError(
                reason="invalid_resource",
                message=f"{relative!r} resolves outside the skill directory {directory}.",
                name=self.name,
                path=directory,
            ) from exc
        return candidate


@dataclass(frozen=True)
class SkillRoot:
    """One usable, existing skill root directory."""

    path: Path
    source: str


@dataclass(frozen=True)
class RootIssue:
    """A problem found with an explicit (`WAKIL_SKILL_PATH`) root."""

    root: Path
    source: str
    reason: str  # "missing" | "not_a_directory"
    message: str


@dataclass(frozen=True)
class RootResolution:
    """The outcome of normalizing and checking the ordered root list."""

    roots: list[SkillRoot]
    issues: list[RootIssue]


@dataclass(frozen=True)
class ResolutionContext:
    """Inputs needed to resolve a skill: the KB root plus the default and override roots."""

    kb_root: Path
    user_skill_root: Path
    builtin_skill_root: Path
    skill_path: str | None = None
