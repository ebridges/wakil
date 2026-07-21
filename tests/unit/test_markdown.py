from pathlib import Path

from wakil.knowledge.markdown import (
    discover_markdown_files,
    read_markdown_file,
    slice_heading_section,
)


def test_discovers_all_markdown_files(kb_path: Path):
    files = discover_markdown_files(kb_path)
    assert len(files) == 8
    assert Path("concepts/graph-memory.md") in files
    assert Path("README.md") in files


def test_skips_wakil_and_hidden_directories(kb_path: Path):
    (kb_path / ".wakil").mkdir()
    (kb_path / ".wakil" / "ignore-me.md").write_text("# hidden")
    (kb_path / ".obsidian").mkdir()
    (kb_path / ".obsidian" / "config.md").write_text("# hidden")

    files = discover_markdown_files(kb_path)
    assert all(".wakil" not in f.parts and ".obsidian" not in f.parts for f in files)


def test_title_from_frontmatter_name(kb_path: Path):
    md = read_markdown_file(kb_path, Path("concepts/graph-memory.md"))
    assert md.title == "Graph Memory"
    assert md.metadata["type"] == "concept"


def test_title_from_frontmatter_title(kb_path: Path):
    md = read_markdown_file(kb_path, Path("meetings/2026/2026-07-01-planning.md"))
    assert md.title == "July Planning"


def test_title_from_first_heading(kb_path: Path):
    md = read_markdown_file(kb_path, Path("drafts/rough-idea.md"))
    assert md.title == "A Rough Idea"
    assert md.metadata == {}


def test_title_falls_back_to_filename(kb_path: Path):
    md = read_markdown_file(kb_path, Path("sources/transcripts/notitle.md"))
    assert md.title == "notitle"


def test_content_hash_changes_with_content(kb_path: Path):
    path = Path("drafts/rough-idea.md")
    before = read_markdown_file(kb_path, path).content_hash
    (kb_path / path).write_text("# A Rough Idea\n\nEdited.\n")
    after = read_markdown_file(kb_path, path).content_hash
    assert before != after


def test_malformed_frontmatter_does_not_break_indexing(kb_path: Path):
    bad = kb_path / "drafts" / "bad.md"
    bad.write_text("---\n: not [valid yaml\n---\n\n# Bad Frontmatter\n")
    md = read_markdown_file(kb_path, Path("drafts/bad.md"))
    assert md.title == "Bad Frontmatter"


def test_slice_heading_section_returns_none_when_no_match():
    text = "# Title\n\nIntro.\n\n## Timeline\n\nSome events.\n"
    assert slice_heading_section(text, "Notes") is None


def test_slice_heading_section_h2_includes_nested_h3_but_stops_at_next_h2():
    text = (
        "# Title\n"
        "\n"
        "## Timeline\n"
        "\n"
        "First event.\n"
        "\n"
        "### Subsection\n"
        "\n"
        "Nested detail.\n"
        "\n"
        "## Notes\n"
        "\n"
        "Should not appear.\n"
    )
    result = slice_heading_section(text, "Timeline")
    assert result == "First event.\n\n### Subsection\n\nNested detail."
    assert "Notes" not in result
    assert "Should not appear" not in result


def test_slice_heading_section_matching_is_case_insensitive_and_trims():
    text = "# Title\n\n##   Timeline  \n\nBody.\n"
    assert slice_heading_section(text, "timeline") == "Body."
    assert slice_heading_section(text, "  TIMELINE  ") == "Body."


def test_slice_heading_section_last_heading_runs_to_end_of_text():
    text = "# Title\n\n## Timeline\n\nEarlier.\n\n## Notes\n\nFinal section.\nMore text.\n"
    assert slice_heading_section(text, "Notes") == "Final section.\nMore text."
