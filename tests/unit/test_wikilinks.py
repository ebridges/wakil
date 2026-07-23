"""Pure-function tests for wikilink parsing/normalization."""

from wakil.knowledge.wikilinks import Wikilink, normalize_target, parse_wikilinks


def test_parse_bare_target():
    links = parse_wikilinks("See [[people/alice]] for details.")
    assert links == [Wikilink(target="people/alice", display=None)]


def test_parse_target_with_display():
    links = parse_wikilinks("Ping [[people/alice|Alice]] tomorrow.")
    assert links == [Wikilink(target="people/alice", display="Alice")]


def test_parse_mixed_forms_and_duplicates_in_source_order():
    text = "[[a]] then [[b/c.md]] then [[a|first again]]"
    links = parse_wikilinks(text)
    assert links == [
        Wikilink(target="a", display=None),
        Wikilink(target="b/c.md", display=None),
        Wikilink(target="a", display="first again"),
    ]


def test_parse_ignores_single_brackets_and_code_spans():
    text = "Not a link: [alice](people/alice.md), but [[people/alice]] is."
    links = parse_wikilinks(text)
    assert links == [Wikilink(target="people/alice", display=None)]


def test_parse_trims_whitespace_in_target():
    links = parse_wikilinks("[[ people/alice ]]")
    assert links == [Wikilink(target="people/alice", display=None)]


def test_parse_empty_text_returns_empty_list():
    assert parse_wikilinks("") == []


def test_normalize_target_strips_trailing_md():
    assert normalize_target("sources/x.md") == "sources/x"


def test_normalize_target_leaves_extensionless_untouched():
    assert normalize_target("people/alice") == "people/alice"


def test_normalize_target_trims_whitespace():
    assert normalize_target("  sources/x.md  ") == "sources/x"


def test_normalize_target_only_strips_final_md():
    # A stem happening to end in "md" (without the dot) is not an extension.
    assert normalize_target("people/ahmed") == "people/ahmed"
