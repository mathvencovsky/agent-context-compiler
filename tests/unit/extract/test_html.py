"""HTML → IR walker: per-block-type shapes, cleanup, fallback, links (SPEC §4)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.extract.html import extract_html
from app.extract.ir import Block, Document, SourceInfo, make_version_id


def _source(final_url: str = "https://docs.example.com/guide/") -> SourceInfo:
    return SourceInfo(
        url="https://docs.example.com/guide",
        final_url=final_url,
        content_type="text/html; charset=utf-8",
        fetched_at=datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
        etag=None,
        last_modified=None,
        content_hash="a" * 64,
        render_mode="http",
    )


# Enough real prose that readability keeps the surrounding <main> content;
# repeated so short structural snippets are not discarded as boilerplate.
_PROSE = (
    "<p>"
    + (
        "This paragraph is deliberately long so the readability algorithm keeps "
        "the surrounding main content intact for the test. " * 3
    )
    + "</p>"
)


def _wrap(main_html: str) -> str:
    return (
        "<html lang='en'><head><title>T</title></head>"
        f"<body><main>{main_html}</main></body></html>"
    )


def _extract(main_html: str) -> Document:
    """Extract a snippet, padding with prose so readability keeps the section."""
    return extract_html(_wrap(f"<h1>Doc</h1>{_PROSE}{main_html}"), _source())


def _types(doc: Document) -> list[str]:
    return [b.type for b in doc.blocks]


def _first(doc: Document, block_type: str) -> Block:
    return next(b for b in doc.blocks if b.type == block_type)


def test_heading_levels_and_path() -> None:
    doc = _extract(
        "<h2>Section</h2><h3>Sub</h3><p>body text content lives here now</p>"
    )
    headings = [b for b in doc.blocks if b.type == "heading"]
    # the injected <h1>Doc</h1> plus the snippet's h2/h3.
    assert [h.level for h in headings] == [1, 2, 3]
    body = [b for b in doc.blocks if b.type == "paragraph" and "body text" in b.text]
    assert body[0].heading_path == ["Doc", "Section", "Sub"]


def test_paragraph_block() -> None:
    doc = _extract("<p>A distinctive snippet paragraph marker.</p>")
    para = next(b for b in doc.blocks if b.type == "paragraph" and "snippet" in b.text)
    assert para.type == "paragraph"
    assert para.text == "A distinctive snippet paragraph marker."


def test_list_nested_two_space_indent() -> None:
    doc = _extract("<ul><li>one</li><li>two<ul><li>nested</li></ul></li></ul>")
    lst = _first(doc, "list")
    assert lst.data["ordered"] is False
    assert lst.data["items"] == ["one", "two", "  nested"]


def test_ordered_list_flag() -> None:
    doc = _extract("<ol><li>first</li><li>second</li></ol>")
    assert _first(doc, "list").data["ordered"] is True


def test_code_byte_for_byte_and_language() -> None:
    raw = "def f():\n    return 1\n"
    doc = _extract(f'<pre><code class="language-python">{raw}</code></pre>')
    code = _first(doc, "code")
    assert code.data["language"] == "python"
    assert code.data["code"] == raw
    # text equals the raw code (no whitespace normalization).
    assert code.text == raw


def test_table_with_header_and_merged_cells() -> None:
    html = (
        "<table><caption>Cap</caption>"
        "<thead><tr><th>A</th><th>B</th></tr></thead>"
        '<tbody><tr><td>1</td><td rowspan="2">x</td></tr>'
        "<tr><td>2</td></tr></tbody></table>"
    )
    doc = _extract(html)
    table = _first(doc, "table")
    assert table.data["caption"] == "Cap"
    assert table.data["headers"] == ["A", "B"]
    # rowspan repeats "x" into the second row.
    assert table.data["rows"] == [["1", "x"], ["2", "x"]]


def test_table_colspan_repeated() -> None:
    html = (
        '<table><tr><th colspan="2">Wide</th></tr><tr><td>a</td><td>b</td></tr></table>'
    )
    doc = _extract(html)
    table = _first(doc, "table")
    assert table.data["headers"] == ["Wide", "Wide"]


def test_table_no_header_warning() -> None:
    doc = _extract("<table><tr><td>no</td><td>head</td></tr></table>")
    table = _first(doc, "table")
    assert table.data["headers"] == []
    warning = next(w for w in doc.warnings if w.code == "table_no_header")
    assert warning.block_id == table.block_id


def test_blockquote_block() -> None:
    doc = _extract("<blockquote>wise words</blockquote>")
    bq = _first(doc, "blockquote")
    assert bq.data == {"text": "wise words"}


def test_admonition_kind_detection() -> None:
    doc = _extract(
        '<div class="admonition warning"><p>Be careful: this operation permanently '
        "deletes data and cannot be undone once confirmed by the user.</p></div>"
    )
    adm = _first(doc, "admonition")
    assert adm.data == {"kind": "warning"}


def test_admonition_role_note_default() -> None:
    doc = _extract(
        '<div role="note"><p>Heads up: the following configuration keys are '
        "optional and fall back to sensible documented defaults when omitted.</p></div>"
    )
    adm = _first(doc, "admonition")
    assert adm.data == {"kind": "note"}


def test_definition_list_block() -> None:
    doc = _extract("<dl><dt>Term</dt><dd>Definition</dd></dl>")
    dl = _first(doc, "definition_list")
    assert dl.data == {"items": [{"term": "Term", "definition": "Definition"}]}


def test_cleanup_removes_chrome_copy_and_permalink() -> None:
    html = (
        "<nav>NAVIGATION</nav>"
        '<h1>Title<a class="headerlink" href="#t">¶</a></h1>'
        "<p>real content paragraph</p>"
        '<button class="copy">Copy</button>'
        "<footer>FOOTERTEXT</footer>"
        "<aside>SIDEBAR</aside>"
    )
    doc = _extract(html)
    joined = " ".join(b.text for b in doc.blocks)
    assert "NAVIGATION" not in joined
    assert "FOOTERTEXT" not in joined
    assert "SIDEBAR" not in joined
    assert "Copy" not in joined
    assert "¶" not in joined
    # the snippet heading keeps only its own text (no permalink glyph).
    titles = [b.text for b in doc.blocks if b.type == "heading"]
    assert "Title" in titles


def test_absolute_link_resolution() -> None:
    doc = _extract('<p>Linkmarker see <a href="../errors">errors</a> page.</p>')
    para = next(
        b for b in doc.blocks if b.type == "paragraph" and "Linkmarker" in b.text
    )
    assert len(para.links) == 1
    assert para.links[0].href == "https://docs.example.com/errors"
    assert para.links[0].text == "errors"


def test_readability_fallback_preserves_dropped_table() -> None:
    # readability tends to drop a lone table surrounded by very little prose;
    # the fallback must re-extract from <main> and warn.
    html = _wrap(
        "<h1>Data</h1>"
        "<table><thead><tr><th>A</th></tr></thead>"
        "<tbody><tr><td>1</td></tr></tbody></table>"
    )
    doc = extract_html(html, _source())
    assert any(b.type == "table" for b in doc.blocks)
    assert any(w.code == "readability_fallback" for w in doc.warnings)


def test_document_ids_and_metadata() -> None:
    doc = _extract("<h1>Title</h1><p>content body goes here now</p>")
    assert doc.document_id.startswith("doc_")
    assert doc.version_id == make_version_id(doc.document_id, "a" * 64)
    assert doc.metadata.title == "T"
    assert doc.metadata.lang == "en"


def test_extraction_is_deterministic() -> None:
    html = _wrap("<h1>H</h1><p>text</p><table><tr><td>x</td></tr></table>")
    first = extract_html(html, _source())
    second = extract_html(html, _source())
    assert first.model_dump() == second.model_dump()


# A PostgreSQL-style page: no <main>/<article>/role="main", a header table of
# links, then the content in a plain <div id="docContent">.
_NO_MAIN = (
    "<html><body><div class='navheader'><nav>Prev Up Next</nav></div>"
    "<div id='docContent'>"
    "<h2>9.4. String Functions</h2>"
    "<p>This section describes functions for examining strings.</p>"
    "<h3>9.4.1. format</h3>"
    "<p>The function format produces output formatted like sprintf.</p>"
    "<div class='table'><table><tr><th>Function</th></tr>"
    "<tr><td>ascii ( text ) returns the numeric code of the first character.</td>"
    "</tr></table></div></div>"
    "<footer>Copyright</footer></body></html>"
)


def test_dominant_container_descends_to_the_content_div() -> None:
    import lxml.html

    from app.extract.html import _dominant_container

    tree = lxml.html.fromstring(_NO_MAIN)
    assert _dominant_container(tree, {}).get("id") == "docContent"


def test_without_main_a_readability_pick_of_one_table_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lxml.html

    one_table = lxml.html.fromstring(
        "<div><table><tr><td>ascii ( text ) returns the numeric code of the first "
        "character.</td></tr></table></div>"
    )
    monkeypatch.setattr("app.extract.html._readability_subtree", lambda text: one_table)
    doc = extract_html(_NO_MAIN, _source())
    text = " ".join(b.text for b in doc.blocks)
    assert "format produces output" in text and "Copyright" not in text
    assert any(w.code == "readability_fallback" for w in doc.warnings)


def test_without_main_a_complete_readability_pick_is_kept() -> None:
    doc = extract_html(_NO_MAIN, _source())
    assert "format produces output" in " ".join(b.text for b in doc.blocks)
    assert not any(w.code == "readability_fallback" for w in doc.warnings)
