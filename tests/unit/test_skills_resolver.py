"""Tests for the core skill resolver (spec §16: Resolution, Invalid overrides,
Path handling). CLI diagnostics are covered elsewhere.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wakil.config.registry import config_home
from wakil.skills import resolver
from wakil.skills.errors import SkillResolutionError
from wakil.skills.models import (
    SKILL_API_VERSION,
    SOURCE_BUILTIN,
    SOURCE_KB_LOCAL,
    SOURCE_OVERRIDE,
    SOURCE_USER,
    ResolutionContext,
)
from wakil.skills.resolver import (
    default_context,
    discover_skill_names,
    find_shadowed_roots,
    parse_skill_path,
    resolve_roots,
    resolve_skill,
    validate_skill_name,
)


def _write_manifest(skill_dir: Path, metadata: dict, body: str = "Body text.\n") -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for key, value in metadata.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    (skill_dir / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")


def _write_skill(root: Path, dirname: str, *, metadata: dict | None = None) -> Path:
    """Create `<root>/<dirname>/SKILL.md` with valid-by-default frontmatter."""
    skill_dir = root / dirname
    meta = {"name": dirname, "skill_api": SKILL_API_VERSION}
    if metadata:
        meta.update(metadata)
    _write_manifest(skill_dir, meta)
    return skill_dir


@pytest.fixture
def kb_root(tmp_path: Path) -> Path:
    root = tmp_path / "kb"
    root.mkdir()
    return root


@pytest.fixture
def user_root(tmp_path: Path) -> Path:
    root = tmp_path / "user-skills"
    root.mkdir()
    return root


@pytest.fixture
def builtin_root(tmp_path: Path) -> Path:
    root = tmp_path / "builtin-skills"
    root.mkdir()
    return root


@pytest.fixture
def context(kb_root: Path, user_root: Path, builtin_root: Path) -> ResolutionContext:
    return ResolutionContext(
        kb_root=kb_root,
        user_skill_root=user_root,
        builtin_skill_root=builtin_root,
    )


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def test_builtin_skill_resolves_when_no_override_exists(context, builtin_root):
    _write_skill(builtin_root, "meeting-synthesis")

    resolved = resolve_skill("meeting-synthesis", context)

    assert resolved.name == "meeting-synthesis"
    assert resolved.source == SOURCE_BUILTIN
    assert resolved.root == builtin_root.resolve()
    assert resolved.directory == builtin_root.resolve() / "meeting-synthesis"
    assert resolved.manifest == resolved.directory / "SKILL.md"
    assert resolved.metadata.name == "meeting-synthesis"
    assert resolved.metadata.skill_api == SKILL_API_VERSION


def test_user_level_skill_overrides_builtin(context, user_root, builtin_root):
    _write_skill(builtin_root, "meeting-synthesis")
    _write_skill(user_root, "meeting-synthesis")

    resolved = resolve_skill("meeting-synthesis", context)

    assert resolved.source == SOURCE_USER
    assert resolved.root == user_root.resolve()


def test_kb_local_skill_overrides_user_and_builtin(context, kb_root, user_root, builtin_root):
    _write_skill(builtin_root, "meeting-synthesis")
    _write_skill(user_root, "meeting-synthesis")
    _write_skill(kb_root / "skills", "meeting-synthesis")

    resolved = resolve_skill("meeting-synthesis", context)

    assert resolved.source == SOURCE_KB_LOCAL
    assert resolved.root == (kb_root / "skills").resolve()


def test_wakil_skill_path_overrides_default_roots(kb_root, user_root, builtin_root, tmp_path):
    override_root = tmp_path / "experimental-skills"
    override_root.mkdir()
    _write_skill(override_root, "meeting-synthesis")
    _write_skill(kb_root / "skills", "meeting-synthesis")
    _write_skill(user_root, "meeting-synthesis")
    _write_skill(builtin_root, "meeting-synthesis")

    context = ResolutionContext(
        kb_root=kb_root,
        user_skill_root=user_root,
        builtin_skill_root=builtin_root,
        skill_path=str(override_root),
    )

    resolved = resolve_skill("meeting-synthesis", context)

    assert resolved.source == SOURCE_OVERRIDE
    assert resolved.root == override_root.resolve()


def test_first_matching_implementation_is_selected(kb_root, user_root, builtin_root, tmp_path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    _write_skill(second_root, "meeting-synthesis")
    _write_skill(first_root, "meeting-synthesis")

    context = ResolutionContext(
        kb_root=kb_root,
        user_skill_root=user_root,
        builtin_skill_root=builtin_root,
        skill_path=f"{first_root}{os.pathsep}{second_root}",
    )

    resolved = resolve_skill("meeting-synthesis", context)

    assert resolved.root == first_root.resolve()


# --------------------------------------------------------------------------
# Invalid overrides
# --------------------------------------------------------------------------


def test_missing_skill_md_blocks_fallback(context, kb_root, builtin_root):
    (kb_root / "skills" / "meeting-synthesis").mkdir(parents=True)
    _write_skill(builtin_root, "meeting-synthesis")

    with pytest.raises(SkillResolutionError) as excinfo:
        resolve_skill("meeting-synthesis", context)

    assert excinfo.value.reason == "invalid_directory"


def test_malformed_frontmatter_blocks_fallback(context, kb_root, builtin_root):
    skill_dir = kb_root / "skills" / "meeting-synthesis"
    skill_dir.mkdir(parents=True)
    # A literal tab is not valid YAML indentation and reliably fails to parse.
    (skill_dir / "SKILL.md").write_text(
        "---\n\tname: meeting-synthesis\n\tskill_api: 1\n---\nBody\n", encoding="utf-8"
    )
    _write_skill(builtin_root, "meeting-synthesis")

    with pytest.raises(SkillResolutionError) as excinfo:
        resolve_skill("meeting-synthesis", context)

    assert excinfo.value.reason == "invalid_metadata"


def test_mismatched_metadata_name_blocks_fallback(context, kb_root, builtin_root):
    _write_skill(kb_root / "skills", "meeting-synthesis", metadata={"name": "other-name"})
    _write_skill(builtin_root, "meeting-synthesis")

    with pytest.raises(SkillResolutionError) as excinfo:
        resolve_skill("meeting-synthesis", context)

    assert excinfo.value.reason == "invalid_metadata"


def test_unsupported_skill_api_blocks_fallback(context, kb_root, builtin_root):
    _write_skill(kb_root / "skills", "meeting-synthesis", metadata={"skill_api": 999})
    _write_skill(builtin_root, "meeting-synthesis")

    with pytest.raises(SkillResolutionError) as excinfo:
        resolve_skill("meeting-synthesis", context)

    assert excinfo.value.reason == "unsupported_api"


def test_unreadable_selected_skill_blocks_fallback(context, kb_root, builtin_root):
    skill_dir = kb_root / "skills" / "meeting-synthesis"
    skill_dir.mkdir(parents=True)
    # SKILL.md as a directory instead of a file: portable way to make it unreadable.
    (skill_dir / "SKILL.md").mkdir()
    _write_skill(builtin_root, "meeting-synthesis")

    with pytest.raises(SkillResolutionError) as excinfo:
        resolve_skill("meeting-synthesis", context)

    assert excinfo.value.reason == "invalid_directory"


# --------------------------------------------------------------------------
# Path handling
# --------------------------------------------------------------------------


def test_missing_default_roots_are_ignored(tmp_path, builtin_root):
    context = ResolutionContext(
        kb_root=tmp_path / "no-such-kb",
        user_skill_root=tmp_path / "no-such-user-root",
        builtin_skill_root=builtin_root,
    )

    result = resolve_roots(context)

    assert result.issues == []
    assert [root.source for root in result.roots] == [SOURCE_BUILTIN]


def test_non_directory_default_root_is_ignored(kb_root, user_root, builtin_root):
    # kb-local skills root is a plain file, not a directory, and is not an
    # explicit WAKIL_SKILL_PATH entry — per spec §7 this is silently dropped,
    # just like a missing default root, rather than blocking resolution.
    (kb_root / "skills").write_text("not a directory\n", encoding="utf-8")
    _write_skill(user_root, "meeting-synthesis")
    context = ResolutionContext(
        kb_root=kb_root, user_skill_root=user_root, builtin_skill_root=builtin_root
    )

    result = resolve_roots(context)
    assert result.issues == []
    assert [root.source for root in result.roots] == [SOURCE_USER, SOURCE_BUILTIN]

    resolved = resolve_skill("meeting-synthesis", context)
    assert resolved.source == SOURCE_USER


def test_duplicate_roots_are_removed(kb_root, user_root, builtin_root):
    # WAKIL_SKILL_PATH points at the same physical directory as kb-local skills.
    context = ResolutionContext(
        kb_root=kb_root,
        user_skill_root=user_root,
        builtin_skill_root=builtin_root,
        skill_path=str(kb_root / "skills"),
    )
    (kb_root / "skills").mkdir()

    result = resolve_roots(context)

    normalized_paths = [root.path for root in result.roots]
    assert normalized_paths.count((kb_root / "skills").resolve()) == 1
    # First occurrence (the explicit override) wins the source label.
    override_entry = next(r for r in result.roots if r.path == (kb_root / "skills").resolve())
    assert override_entry.source == SOURCE_OVERRIDE


def test_tilde_expands_in_skill_path(monkeypatch, tmp_path, kb_root, user_root, builtin_root):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / "experimental-skills").mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    context = ResolutionContext(
        kb_root=kb_root,
        user_skill_root=user_root,
        builtin_skill_root=builtin_root,
        skill_path="~/experimental-skills",
    )

    result = resolve_roots(context)

    assert (fake_home / "experimental-skills").resolve() in [r.path for r in result.roots]


def test_environment_variables_expand_in_skill_path(
    monkeypatch, tmp_path, kb_root, user_root, builtin_root
):
    extra_root = tmp_path / "env-skills"
    extra_root.mkdir()
    monkeypatch.setenv("WAKIL_TEST_SKILL_ROOT", str(extra_root))

    context = ResolutionContext(
        kb_root=kb_root,
        user_skill_root=user_root,
        builtin_skill_root=builtin_root,
        skill_path="$WAKIL_TEST_SKILL_ROOT",
    )

    result = resolve_roots(context)

    assert extra_root.resolve() in [r.path for r in result.roots]


def test_empty_path_list_entries_are_ignored(tmp_path):
    root_a = tmp_path / "a"

    segments = parse_skill_path(f"{os.pathsep}{root_a}{os.pathsep}{os.pathsep}")

    assert segments == [Path(str(root_a))]
    assert Path.cwd() not in segments


@pytest.mark.parametrize(
    "name",
    [
        "",
        "Foo",
        "foo/bar",
        "..",
        ".",
        "foo bar",
        "-foo",
        "foo-",
        "foo_bar",
        "../../etc",
        "foo..bar",
    ],
)
def test_invalid_skill_names_are_rejected(name, context):
    with pytest.raises(SkillResolutionError) as excinfo:
        validate_skill_name(name)
    assert excinfo.value.reason == "invalid_name"

    with pytest.raises(SkillResolutionError) as excinfo:
        resolve_skill(name, context)
    assert excinfo.value.reason == "invalid_name"


def test_relative_supporting_files_resolve_within_selected_directory(
    context, kb_root, builtin_root
):
    kb_skill_dir = _write_skill(kb_root / "skills", "meeting-synthesis")
    (kb_skill_dir / "templates").mkdir()
    (kb_skill_dir / "templates" / "note.md").write_text("template\n", encoding="utf-8")

    # A same-named builtin skill also has a resource at that relative path, to prove
    # resolution never borrows it once the kb-local directory has won.
    builtin_skill_dir = _write_skill(builtin_root, "meeting-synthesis")
    (builtin_skill_dir / "templates").mkdir()
    (builtin_skill_dir / "templates" / "note.md").write_text("builtin template\n", encoding="utf-8")

    resolved = resolve_skill("meeting-synthesis", context)
    resource_path = resolved.resource("templates/note.md")

    assert resource_path == kb_skill_dir.resolve() / "templates" / "note.md"
    assert resource_path.read_text(encoding="utf-8") == "template\n"


@pytest.mark.parametrize(
    "relative",
    ["..", "../../../../etc/passwd", "sub/../../evil", "/etc/passwd"],
)
def test_resource_rejects_paths_escaping_the_skill_directory(
    relative, context, kb_root, builtin_root
):
    _write_skill(kb_root / "skills", "meeting-synthesis")
    resolved = resolve_skill("meeting-synthesis", context)

    with pytest.raises(SkillResolutionError) as excinfo:
        resolved.resource(relative)
    assert excinfo.value.reason == "invalid_resource"


# --------------------------------------------------------------------------
# Discovery helpers (not in spec §16's required list, but part of the contract)
# --------------------------------------------------------------------------


def test_discover_skill_names_lists_candidates_across_roots(context, kb_root, user_root):
    _write_skill(kb_root / "skills", "note-routing")
    _write_skill(user_root, "entity-resolution")

    names = discover_skill_names(context)

    assert names == ["entity-resolution", "note-routing"]


def test_find_shadowed_roots_reports_precedence_order(context, kb_root, user_root, builtin_root):
    _write_skill(kb_root / "skills", "meeting-synthesis")
    _write_skill(user_root, "meeting-synthesis")
    _write_skill(builtin_root, "meeting-synthesis")

    shadowed = find_shadowed_roots("meeting-synthesis", context)

    assert [root.source for root in shadowed] == [SOURCE_KB_LOCAL, SOURCE_USER, SOURCE_BUILTIN]
    assert shadowed[0].path == (kb_root / "skills").resolve()


def test_find_shadowed_roots_rejects_invalid_name(context):
    with pytest.raises(SkillResolutionError) as excinfo:
        find_shadowed_roots("../../../../etc", context)
    assert excinfo.value.reason == "invalid_name"


# --------------------------------------------------------------------------
# default_context
# --------------------------------------------------------------------------


def test_default_context_reads_env_and_uses_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("WAKIL_SKILL_PATH", str(tmp_path / "experimental-skills"))
    kb_root = tmp_path / "kb"

    context = default_context(kb_root)

    assert context.kb_root == kb_root
    assert context.skill_path == str(tmp_path / "experimental-skills")
    assert context.user_skill_root == config_home() / "skills"
    assert context.builtin_skill_root == Path(resolver.__file__).resolve().parent / "builtin"
