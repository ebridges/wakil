"""Tests for the skill catalog's content-quality lint (wakil.skills.lint)."""

from __future__ import annotations

from pathlib import Path

import pytest

from wakil.skills import resolver
from wakil.skills.lint import builtin_catalog_names, lint_skill
from wakil.skills.models import ResolutionContext
from wakil.skills.resolver import resolve_skill

BUILTIN_ROOT = Path(resolver.__file__).resolve().parent
BUILTIN_NAMES = builtin_catalog_names(BUILTIN_ROOT)


def _builtin_context(tmp_path: Path) -> ResolutionContext:
    """A context that resolves only against the real, shipped builtin catalog."""
    return ResolutionContext(
        kb_root=tmp_path / "no-such-kb",
        user_skill_root=tmp_path / "no-such-user-root",
        builtin_skill_root=BUILTIN_ROOT,
    )


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str = "Do the thing. Use when the thing needs doing.",
    body: str = "## When to use\n\nAlways.\n",
) -> Path:
    """Write a minimal but well-formed skill at root/skills/name/SKILL.md."""
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nskill_api: 1\n---\n\n# {name}\n\n{body}",
        encoding="utf-8",
    )
    return skill_dir


def _resolve_kb_local(tmp_path: Path, name: str):
    """Resolve a kb-local-only skill written under tmp_path/skills/<name>."""
    context = ResolutionContext(
        kb_root=tmp_path,
        user_skill_root=tmp_path / "no-such-user-root",
        builtin_skill_root=tmp_path / "no-such-builtin-root",
    )
    return resolve_skill(name, context)


# --------------------------------------------------------------------------
# Every real, shipped skill lints clean
# --------------------------------------------------------------------------


def test_builtin_catalog_names_discovers_the_real_catalog():
    # Sanity check: this is what drives the parametrization below, and it must
    # be discovered live (not a hardcoded list) or a 13th skill silently
    # wouldn't get covered.
    assert len(BUILTIN_NAMES) >= 1
    assert sorted(BUILTIN_NAMES) == BUILTIN_NAMES


@pytest.mark.parametrize("skill_name", BUILTIN_NAMES)
def test_real_builtin_skill_has_no_lint_findings(skill_name: str, tmp_path: Path):
    context = _builtin_context(tmp_path)
    resolved = resolve_skill(skill_name, context)

    findings = lint_skill(resolved, BUILTIN_NAMES)

    assert findings == [], [(f.check, f.message) for f in findings]


# --------------------------------------------------------------------------
# Narrow tests: prove each check actually fires, not just passes vacuously
# --------------------------------------------------------------------------


def test_time_sensitive_phrasing_is_flagged(tmp_path: Path):
    _write_skill(
        tmp_path,
        "stale-phrasing",
        body="## When to use\n\nAs of today, this is the only way to do it.\n",
    )
    resolved = _resolve_kb_local(tmp_path, "stale-phrasing")

    findings = lint_skill(resolved, {"stale-phrasing"})

    checks = {f.check for f in findings}
    assert "time-sensitive-phrasing" in checks
    [finding] = [f for f in findings if f.check == "time-sensitive-phrasing"]
    assert "as of today" in finding.message.lower()


def test_dangling_cross_reference_is_flagged(tmp_path: Path):
    _write_skill(
        tmp_path,
        "dangling-ref",
        body="## When to use\n\nHand off to `imaginary-sibling-skill` when done.\n",
    )
    resolved = _resolve_kb_local(tmp_path, "dangling-ref")

    # Simulate the live catalog containing only this one skill — anything
    # else backtick-quoted and catalog-shaped should be flagged as dangling.
    findings = lint_skill(resolved, {"dangling-ref"})

    checks = {f.check for f in findings}
    assert "dangling-cross-reference" in checks
    [finding] = [f for f in findings if f.check == "dangling-cross-reference"]
    assert "imaginary-sibling-skill" in finding.message


def test_orphaned_reference_file_is_flagged(tmp_path: Path):
    skill_dir = _write_skill(
        tmp_path,
        "has-orphan",
        body="## When to use\n\nAlways. No pointer to the reference file below.\n",
    )
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "orphan.md").write_text("Nobody links to me.\n", encoding="utf-8")

    resolved = _resolve_kb_local(tmp_path, "has-orphan")

    findings = lint_skill(resolved, {"has-orphan"})

    checks = {f.check for f in findings}
    assert "orphaned-support-file" in checks
    [finding] = [f for f in findings if f.check == "orphaned-support-file"]
    assert "references/orphan.md" in finding.message
