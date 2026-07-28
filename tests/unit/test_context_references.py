from pathlib import Path

import pytest

from wakil.app import context_references
from wakil.app.context_references import (
    ContextResolutionError,
    expand_piece,
    resolve_context,
)
from wakil.integrations.web import Article, FetchError


def test_bare_word_without_slash_or_extension_is_untouched(tmp_path: Path):
    raw = "ping @someone about the release"
    assert expand_piece(raw, tmp_path) == raw


def test_bare_word_with_slash_is_treated_as_file_reference(tmp_path: Path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "file.md").write_text("Some note content.")

    result = expand_piece("see @notes/file.md for context", tmp_path)
    head, _, _ = result.partition("--- Attached Context ---")

    assert "@notes/file.md" not in head
    assert "Some note content." in result
    assert "--- Attached Context ---" in result


def test_bare_word_with_known_extension_is_treated_as_file_reference(tmp_path: Path):
    (tmp_path / "readme.md").write_text("Readme body.")

    result = expand_piece("check @readme.md please", tmp_path)
    head, _, _ = result.partition("--- Attached Context ---")

    assert "@readme.md" not in head
    assert "Readme body." in result
    assert "--- Attached Context ---" in result


def test_mid_token_at_sign_is_not_treated_as_a_reference(tmp_path: Path):
    raw = "reach out to jane@readme.md about this"
    assert expand_piece(raw, tmp_path) == raw


def test_quoted_file_path_with_spaces(tmp_path: Path):
    (tmp_path / "quoted file.md").write_text("Quoted content.")

    result = expand_piece('use @file:"quoted file.md" here', tmp_path)

    assert "Quoted content." in result
    assert "quoted file.md" not in result.split("--- Attached Context ---")[0]


def test_line_range_slices_only_those_lines(tmp_path: Path):
    (tmp_path / "lines.txt").write_text("line1\nline2\nline3\nline4\nline5\n")

    result = expand_piece("@file:lines.txt:2-4", tmp_path)

    assert "line2\nline3\nline4" in result
    assert "line1" not in result
    assert "line5" not in result


def test_out_of_range_line_numbers_clamp_instead_of_crashing(tmp_path: Path):
    (tmp_path / "lines.txt").write_text("line1\nline2\nline3\n")

    result = expand_piece("@file:lines.txt:10-20", tmp_path)

    assert "line3" in result
    assert "line1" not in result
    assert "line2" not in result


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(ContextResolutionError, match="File not found"):
        expand_piece("@file:does-not-exist.md", tmp_path)


def test_missing_heading_anchor_raises(tmp_path: Path):
    (tmp_path / "doc.md").write_text("# Title\n\nSome text.\n")

    with pytest.raises(ContextResolutionError, match="Heading"):
        expand_piece('@file:"doc.md#Nonexistent Heading"', tmp_path)


def test_relative_escape_outside_workspace_raises(tmp_path: Path):
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    (tmp_path / "outside.md").write_text("secret")

    with pytest.raises(ContextResolutionError, match="outside the workspace"):
        expand_piece("@file:../outside.md", workspace_root)


def test_absolute_path_outside_workspace_raises(tmp_path: Path):
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("secret")

    with pytest.raises(ContextResolutionError, match="outside the workspace"):
        expand_piece(f"@file:{outside}", workspace_root)


def test_binary_file_raises(tmp_path: Path):
    binary_path = tmp_path / "blob.dat"
    binary_path.write_bytes(b"\x00\x01\x02binary\x00data")

    with pytest.raises(ContextResolutionError, match="Cannot include binary file"):
        expand_piece("@file:blob.dat", tmp_path)


def test_known_text_extension_overrides_binary_heuristic(tmp_path: Path):
    md_path = tmp_path / "weird.md"
    md_path.write_bytes(b"# Title\n\x00ambiguous-bytes\x00\n")

    result = expand_piece("@file:weird.md", tmp_path)

    assert "ambiguous-bytes" in result
    assert "--- Attached Context ---" in result


def test_url_reference_success(tmp_path: Path, monkeypatch):
    fetched = Article(url="https://example.com/post", title="Post", text="Article body text.")
    monkeypatch.setattr(context_references, "fetch_article", lambda url: fetched)

    result = expand_piece("read @url:https://example.com/post now", tmp_path)
    head, _, _ = result.partition("--- Attached Context ---")

    assert "@url:https://example.com/post" not in head
    assert "Article body text." in result
    assert "--- Attached Context ---" in result


def test_url_reference_fetch_error_becomes_context_resolution_error(tmp_path: Path, monkeypatch):
    def _raise(url):
        raise FetchError("boom")

    monkeypatch.setattr(context_references, "fetch_article", _raise)

    with pytest.raises(ContextResolutionError, match="Could not fetch"):
        expand_piece("@url:https://example.com/broken", tmp_path)


def test_expand_piece_returns_text_unchanged_when_no_references(tmp_path: Path):
    raw = "no references in this text at all"
    assert expand_piece(raw, tmp_path) == raw
    assert "--- Attached Context ---" not in expand_piece(raw, tmp_path)


def test_expand_piece_strips_tokens_and_appends_section_only_when_resolved(tmp_path: Path):
    (tmp_path / "notes.md").write_text("Note body.")

    result = expand_piece("See @file:notes.md for details", tmp_path)
    head, _, _ = result.partition("--- Attached Context ---")

    assert "@file:notes.md" not in head
    assert result.startswith("See")
    assert result.endswith("```")
    assert result.count("--- Attached Context ---") == 1


def test_expand_piece_collapses_whitespace_left_by_stripped_tokens(tmp_path: Path):
    (tmp_path / "a.md").write_text("A body.")
    (tmp_path / "b.md").write_text("B body.")

    result = expand_piece("see prep documents at @file:a.md and @file:b.md", tmp_path)
    head, _, _ = result.partition("--- Attached Context ---")

    assert head.strip() == "see prep documents at and"
    assert "  " not in head


def test_resolve_context_both_empty_returns_none_and_empty_list():
    result = resolve_context(context=[], context_files=[], workspace_root=Path("/tmp"))
    assert result == (None, [])
    assert result[0] is None


def test_resolve_context_joins_context_then_context_files_in_order(tmp_path: Path):
    file1 = tmp_path / "one.md"
    file1.write_text("file one content")
    file2 = tmp_path / "two.md"
    file2.write_text("file two content")

    resolved, warnings = resolve_context(
        context=["piece one", "piece two"],
        context_files=[file1, file2],
        workspace_root=tmp_path,
    )

    assert resolved is not None
    assert resolved.text == "\n\n---\n\n".join(
        ["piece one", "piece two", "file one content", "file two content"]
    )
    # No @file:/@url: references were expanded, so the digest matches the text.
    assert resolved.digest == resolved.text
    assert resolved.referenced_paths == []
    assert warnings == []


def test_context_file_body_references_are_expanded(tmp_path: Path):
    other = tmp_path / "other.md"
    other.write_text("Other note content.")
    context_file = tmp_path / "context.md"
    context_file.write_text("Background: @file:other.md")

    resolved, _ = resolve_context(context=[], context_files=[context_file], workspace_root=tmp_path)

    assert resolved is not None
    head, _, _ = resolved.text.partition("--- Attached Context ---")
    assert "@file:other.md" not in head
    assert "Other note content." in resolved.text
    assert "--- Attached Context ---" in resolved.text


def test_resolve_context_digest_excludes_attached_context_block(tmp_path: Path):
    other = tmp_path / "other.md"
    other.write_text("Other note content.")
    context_file = tmp_path / "context.md"
    context_file.write_text("Background: @file:other.md")

    resolved, _ = resolve_context(context=[], context_files=[context_file], workspace_root=tmp_path)

    assert resolved is not None
    assert resolved.digest == "Background:"
    assert "Other note content." not in resolved.digest
    assert "--- Attached Context ---" not in resolved.digest


def test_resolve_context_returns_referenced_file_paths(tmp_path: Path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "other.md").write_text("Other note content.")

    resolved, _ = resolve_context(
        context=["see @file:notes/other.md for background"],
        context_files=[],
        workspace_root=tmp_path,
    )

    assert resolved is not None
    assert resolved.referenced_paths == ["notes/other.md"]


def test_resolve_context_referenced_paths_deduped_across_pieces(tmp_path: Path):
    (tmp_path / "shared.md").write_text("Shared content.")

    resolved, _ = resolve_context(
        context=["@file:shared.md", "@file:shared.md"],
        context_files=[],
        workspace_root=tmp_path,
    )

    assert resolved is not None
    assert resolved.referenced_paths == ["shared.md"]


def test_resolve_context_warns_between_25_and_50_percent_budget(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(context_references, "MODEL_CONTEXT_WINDOW_TOKENS", 100)

    resolved, warnings = resolve_context(
        context=["x" * 140], context_files=[], workspace_root=tmp_path
    )

    assert resolved is not None
    assert len(warnings) == 1
    assert "35 tokens" in warnings[0]
    assert "25-token soft budget" in warnings[0]


def test_resolve_context_raises_over_50_percent_budget(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(context_references, "MODEL_CONTEXT_WINDOW_TOKENS", 100)

    with pytest.raises(ContextResolutionError, match="too large"):
        resolve_context(context=["y" * 300], context_files=[], workspace_root=tmp_path)
