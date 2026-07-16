"""Exceptions raised during skill resolution."""

from __future__ import annotations

from pathlib import Path


class SkillResolutionError(Exception):
    """A skill could not be resolved or validated.

    `reason` is one of: invalid_name, invalid_root, not_found,
    invalid_directory, invalid_metadata, unsupported_api, invalid_resource
    (see spec §11 and §14).
    """

    def __init__(
        self,
        reason: str,
        message: str,
        *,
        name: str | None = None,
        path: Path | None = None,
        searched_roots: list[Path] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.name = name
        self.path = path
        self.searched_roots = searched_roots or []
