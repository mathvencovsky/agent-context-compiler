"""``llms.txt`` parsing: the link list only, every field made harmless (J3)."""

from __future__ import annotations

from app.extract.llms_txt import MAX_NOTE_CHARS, parse_llms_txt

BASE = "https://docs.example.com/llms.txt"

SAMPLE = """# Example Docs

> Example is a library for examples.
> It has two lines of summary.

Some free text that is not a link list.

## Docs

- [Quick start](https://docs.example.com/start.md): Install and run
- [API reference](/api.md)
* [Config](config/index.md "Config page") - every option

## Optional

- [Changelog](https://docs.example.com/changelog.md)
"""


def test_parses_title_summary_sections_and_links() -> None:
    index = parse_llms_txt(SAMPLE, BASE)
    assert index.title == "Example Docs"
    assert index.summary == (
        "Example is a library for examples. It has two lines of summary."
    )
    assert [(e.section, e.title, e.url, e.note) for e in index.entries] == [
        ("Docs", "Quick start", "https://docs.example.com/start.md", "Install and run"),
        ("Docs", "API reference", "https://docs.example.com/api.md", ""),
        ("Docs", "Config", "https://docs.example.com/config/index.md", "every option"),
        ("Optional", "Changelog", "https://docs.example.com/changelog.md", ""),
    ]
    assert index.dropped == 0


def test_unsafe_urls_are_dropped_and_counted() -> None:
    secret = "https://docs.example.com/a?api_key=sk-abcdefghijklmnopqrstuvwxyz0123"
    text = "\n".join(
        [
            "## Links",
            "- [js](javascript:void0)",
            "- [data](data:text/html,hi)",
            "- [creds](https://user:pass@docs.example.com/a)",
            "- [ftp](ftp://docs.example.com/a)",
            f"- [key]({secret})",
            "- [ok](https://docs.example.com/ok)",
        ]
    )
    index = parse_llms_txt(text, BASE)
    assert [e.title for e in index.entries] == ["ok"]
    assert index.dropped == 5


def test_text_fields_are_one_line_without_invisible_characters() -> None:
    long_note = "x" * (MAX_NOTE_CHARS + 50)
    text = (
        "# Ti​tle\n"
        f"- [A‮B](https://docs.example.com/a): {long_note}\n"
        "- [C\x1b[31m](https://docs.example.com/c): note\n"
    )
    index = parse_llms_txt(text, BASE)
    assert index.title == "Title"
    assert index.invisible_removed >= 2
    assert [e.title for e in index.entries][0] == "AB"
    first = index.entries[0]
    assert len(first.note) == MAX_NOTE_CHARS and first.note.endswith("…")
    assert all("\x1b" not in e.title for e in index.entries)


def test_links_inside_code_blocks_are_ignored() -> None:
    text = (
        "```\n- [no](https://docs.example.com/no)\n```\n"
        "- [yes](https://docs.example.com/y)\n"
    )
    assert [e.title for e in parse_llms_txt(text, BASE).entries] == ["yes"]


def test_a_file_without_links_has_no_entries() -> None:
    index = parse_llms_txt("# Only a title\n\nProse.\n", BASE)
    assert index.entries == [] and index.title == "Only a title"


def test_empty_title_falls_back_to_the_url() -> None:
    index = parse_llms_txt("- [](https://docs.example.com/x)\n", BASE)
    assert index.entries[0].title == "https://docs.example.com/x"


def test_hostile_long_lines_parse_in_linear_time() -> None:
    import time

    start = time.monotonic()
    parse_llms_txt("# " + " " * 300_000 + "x\n- [" + "a" * 300_000 + "\n", BASE)
    assert time.monotonic() - start < 2.0  # a quadratic regex takes minutes


def test_closing_hashes_are_not_part_of_the_section_name() -> None:
    index = parse_llms_txt("## Guides ##\n- [a](https://docs.example.com/a)\n", BASE)
    assert index.entries[0].section == "Guides"
