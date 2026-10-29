"""Markdown and plain-text extraction to the IR (SPEC §4, M10b)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from app.extract.ir import SourceInfo
from app.extract.markdown import extract_markdown, extract_plain_text, is_markdown_url
from app.extract.to_markdown import to_markdown
from app.index.chunker import chunk_document


def _source(
    text: str, url: str = "https://docs.example.com/guide/auth.md"
) -> SourceInfo:
    return SourceInfo(
        url=url,
        final_url=url,
        content_type="text/markdown",
        fetched_at=datetime(2026, 10, 29, tzinfo=UTC),
        etag=None,
        last_modified=None,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        render_mode="http",
    )


def _md(text: str, url: str = "https://docs.example.com/guide/auth.md"):  # type: ignore[no-untyped-def]
    return extract_markdown(text, _source(text, url))


GUIDE = """---
title: "Auth guide"
sidebar: 2
---
# Authentication

Use the `token` with [the API](../api.md) **now**.

## Tokens

> [!WARNING]
> Tokens expire after *one hour*.

> A plain quote.

| Name | Type |
|------|------|
| ttl  | int  |

- one
  - two
    ```bash
    curl -H "Authorization: Bearer $T" https://api.example.com
    ```
- three

1. first
2. second

```python title="client.py" {2}
print("hi")
    indented()
```

Refresh
-------

    four-space indented code
"""


def test_front_matter_is_dropped_and_its_title_wins() -> None:
    doc = _md(GUIDE)
    assert doc.metadata.title == "Auth guide"
    assert all("sidebar" not in b.text for b in doc.blocks)


def test_block_types_and_heading_paths() -> None:
    doc = _md(GUIDE)
    kinds = [(b.type, b.level) for b in doc.blocks]
    assert kinds == [
        ("heading", 1),
        ("paragraph", None),
        ("heading", 2),
        ("admonition", None),
        ("blockquote", None),
        ("table", None),
        ("list", None),
        ("code", None),
        ("list", None),
        ("code", None),
        ("heading", 2),  # setext
        ("code", None),  # indented
    ]
    paragraph = doc.blocks[1]
    assert paragraph.text == "Use the token with the API now."
    assert paragraph.heading_path == ["Authentication"]
    assert paragraph.links[0].href == "https://docs.example.com/api.md"
    assert doc.blocks[3].heading_path == ["Authentication", "Tokens"]
    assert doc.blocks[-1].heading_path == ["Authentication", "Refresh"]


def test_admonition_table_list_and_code_payloads() -> None:
    doc = _md(GUIDE)
    admonition, quote, table, bullets, nested_code, ordered, code = doc.blocks[3:10]
    assert admonition.data == {"kind": "warning"}
    assert admonition.text == "Tokens expire after one hour."
    assert quote.text == "A plain quote."
    assert table.data == {
        "caption": None,
        "headers": ["Name", "Type"],
        "rows": [["ttl", "int"]],
        "notes": [],
    }
    assert bullets.data == {"ordered": False, "items": ["one", "  two", "three"]}
    # Code inside a list item is its own block after the list, never duplicated
    # into the list text.
    assert nested_code.data["language"] == "bash"
    assert "curl" not in bullets.text
    assert ordered.data == {"ordered": True, "items": ["first", "second"]}
    assert code.data == {
        "language": "python",
        "code": 'print("hi")\n    indented()\n',
    }
    assert code.text == code.data["code"]  # code bytes are verbatim


@pytest.mark.parametrize(
    ("alert", "kind"),
    [
        ("NOTE", "note"),
        ("TIP", "tip"),
        ("IMPORTANT", "info"),
        ("WARNING", "warning"),
        ("CAUTION", "danger"),
    ],
)
def test_gfm_alerts_map_to_the_spec_kinds(alert: str, kind: str) -> None:
    doc = _md(f"> [!{alert}]\n> Body text.\n")
    assert [(b.type, b.data, b.text) for b in doc.blocks] == [
        ("admonition", {"kind": kind}, "Body text.")
    ]


def test_round_trip_through_to_markdown_keeps_structure() -> None:
    doc = _md(GUIDE)
    again = _md(to_markdown(doc))
    assert [(b.type, b.text, b.data) for b in again.blocks] == [
        (b.type, b.text, b.data) for b in doc.blocks
    ]


def test_raw_html_is_reduced_to_text_without_scripts_or_comments() -> None:
    doc = _md(
        "<div>\n<script>steal()</script>\nVisible <a href='/x'>link</a>\n</div>\n\n"
        "<!-- an html comment that a human never sees -->\n\n"
        "Inline <span>tag</span> and <script>alert(1)</script> gone.\n"
    )
    texts = [b.text for b in doc.blocks]
    assert texts == ["Visible link", "Inline tag and gone."]
    assert doc.blocks[0].links[0].href == "https://docs.example.com/x"


def test_mdx_callout_component_becomes_an_admonition() -> None:
    doc = _md('<Warning>Rotate keys <a href="/keys">often</a>.</Warning>\n')
    block = doc.blocks[0]
    assert (block.type, block.data, block.text) == (
        "admonition",
        {"kind": "warning"},
        "Rotate keys often.",
    )
    assert block.links[0].href == "https://docs.example.com/keys"


def test_images_are_skipped_and_title_falls_back_to_the_first_h1() -> None:
    doc = _md("Intro\n\n# Real title\n\n![alt text](img.png) Caption\n")
    assert doc.metadata.title == "Real title"
    assert doc.blocks[-1].text == "Caption"


def test_invisible_unicode_is_stripped_with_one_warning() -> None:
    doc = _md("# Ti​tle\n\nHello\U000e0041\U000e0042 world\n")
    assert [b.text for b in doc.blocks] == ["Title", "Hello world"]
    assert doc.blocks[1].heading_path == ["Title"]
    assert [w.code for w in doc.warnings] == ["invisible_text_removed"]


def test_ids_are_stable_and_deterministic() -> None:
    first, second = _md(GUIDE), _md(GUIDE)
    assert [b.block_id for b in first.blocks] == [b.block_id for b in second.blocks]
    assert len({b.block_id for b in first.blocks}) == len(first.blocks)
    assert [b.order for b in first.blocks] == list(range(len(first.blocks)))
    assert all(b.page is None for b in first.blocks)


def test_heading_aware_chunking_follows_markdown_sections() -> None:
    sections = "\n\n".join(
        f"## Section {i}\n\n" + " ".join(f"word{i}x{j}" for j in range(40))
        for i in range(3)
    )
    doc = _md(f"# Guide\n\n{sections}\n")
    chunks = chunk_document(doc)
    for i in range(3):
        (body,) = [c for c in chunks if f"word{i}x0" in c.text]
        assert body.heading_path == ["Guide", f"Section {i}"]
        assert body.text.startswith(f"Guide > Section {i}\n\n")
    assert all(c.block_ids for c in chunks)


def test_empty_markdown_has_no_blocks() -> None:
    assert _md("   \n\n<!-- nothing -->\n").blocks == []


def test_deep_nesting_is_bounded() -> None:
    doc = _md(">" * 5000 + " deep\n" + "- " * 2000 + "item\n")
    assert doc.blocks  # parsed without recursion errors


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://raw.githubusercontent.com/o/r/main/README.md", True),
        ("https://docs.example.com/llms.txt", True),
        ("https://docs.example.com/llms-full.txt?x=1", True),
        ("https://docs.example.com/page.mdx", True),
        ("https://www.rfc-editor.org/rfc/rfc2046.txt", False),
        ("https://docs.python.org/3/_sources/library/os.rst.txt", False),
    ],
)
def test_markdown_urls_served_as_plain_text(url: str, expected: bool) -> None:
    assert is_markdown_url(url) is expected


RFC = """\



Network Working Group                                          N. Freed
Request for Comments: 2046                                     Innosoft
Category: Standards Track                                 November 1996


                 Multipurpose Internet Mail Extensions
                            (MIME) Part Two:
                             Media Types

Status of this Memo

   This document specifies an Internet standards track protocol for the
   Internet community.

1.  Introduction

   The first document in this set, RFC 2045, defines a number of header
   fields, including Content-Type.

   (1)   a name and a description of the type, including
         criteria for whether a particular type would qualify

     +--------+       +--------+
     | client | ----> | server |
     +--------+       +--------+



Freed & Borenstein          Standards Track                     [Page 2]
\f
RFC 2046                      Media Types                  November 1996


   Text continues after the page break.

1.1.  Scope

     media-type   = type "/" subtype
     type         = token
"""


def test_plain_text_rfc_reads_as_sections_paragraphs_and_figures() -> None:
    doc = extract_plain_text(
        RFC, _source(RFC, "https://www.rfc-editor.org/rfc/rfc2046.txt")
    )
    assert (
        doc.metadata.title
        == "Multipurpose Internet Mail Extensions (MIME) Part Two: Media Types"
    )
    kinds = [(b.type, b.level, b.text[:30]) for b in doc.blocks]
    assert kinds == [
        ("code", None, "Network Working Group         "),
        ("paragraph", None, "Multipurpose Internet Mail Ext"),
        ("paragraph", None, "Status of this Memo"),
        ("paragraph", None, "This document specifies an Int"),
        ("heading", 1, "1. Introduction"),
        ("paragraph", None, "The first document in this set"),
        ("paragraph", None, "(1) a name and a description o"),
        ("code", None, "+--------+       +--------+\n| "),
        ("paragraph", None, "Text continues after the page "),
        ("heading", 2, "1.1. Scope"),
        ("code", None, 'media-type   = type "/" subtyp'),
    ]
    # Page furniture is gone; the paragraph after the page break stands alone.
    assert not any("[Page 2]" in b.text for b in doc.blocks)
    assert not any(b.text.startswith("RFC 2046") for b in doc.blocks)
    assert doc.blocks[-1].heading_path == ["1. Introduction", "1.1. Scope"]
    figure = doc.blocks[7].data["code"]
    assert figure == (
        "+--------+       +--------+\n"
        "| client | ----> | server |\n"
        "+--------+       +--------+\n"
    )


def test_plain_text_without_structure_is_paragraphs() -> None:
    text = "First line\nwraps here.\n\nSecond paragraph.\n"
    doc = extract_plain_text(text, _source(text, "https://e.example.com/notes.txt"))
    assert [(b.type, b.text) for b in doc.blocks] == [
        ("paragraph", "First line wraps here."),
        ("paragraph", "Second paragraph."),
    ]
    assert doc.metadata.title == "First line wraps here."
