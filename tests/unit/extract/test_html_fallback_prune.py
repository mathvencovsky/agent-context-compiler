"""The readability fallback keeps the container's structure but not its leftovers.

Losing one table or code block used to switch to the whole ``<main>`` and bring
back everything readability had cleaned away (recommended lists, buy buttons).
Now only what readability dropped is pruned, and ``pre``/``table`` are restored.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.extract.html import extract_html
from app.extract.ir import Document, SourceInfo

_PROSE = (
    "<p>"
    + "The widget parser reads a configuration file and builds a tree. " * 8
    + "</p>"
)


def _document(main_html: str) -> Document:
    html = (
        "<html lang='en'><head><title>T</title></head><body>"
        f"<main><h1>Widget</h1>{_PROSE}{main_html}{_PROSE}</main></body></html>"
    )
    source = SourceInfo(
        url="https://shop.example.com/widget",
        final_url="https://shop.example.com/widget",
        content_type="text/html; charset=utf-8",
        fetched_at=datetime(2026, 11, 4, tzinfo=UTC),
        etag=None,
        last_modified=None,
        content_hash="d" * 64,
        render_mode="http",
    )
    return extract_html(html, source)


def _text(doc: Document) -> str:
    return " ".join(b.text for b in doc.blocks)


def test_code_stays_and_shop_text_goes() -> None:
    doc = _document(
        "<pre><code>widget --run fast</code></pre>"
        '<div class="recommended"><ul>'
        '<li><a href="/a">Gadget Alpha</a></li><li><a href="/b">Gadget Beta</a></li>'
        "</ul></div>"
        "<button>Add to cart</button>"
    )
    text = _text(doc)
    assert any(b.type == "code" and "widget --run fast" in b.text for b in doc.blocks)
    assert "Gadget Alpha" not in text
    assert "Add to cart" not in text


def test_real_content_list_is_kept() -> None:
    doc = _document(
        "<pre><code>widget --run fast</code></pre>"
        "<ul><li>Reads the configuration file from the current directory.</li>"
        "<li>Builds the tree and reports every unknown key it finds.</li></ul>"
    )
    text = _text(doc)
    assert "Reads the configuration file from the current directory." in text
    assert "reports every unknown key" in text


def test_table_dropped_by_readability_is_restored() -> None:
    doc = _document(
        "<table><tr><th>Flag</th><th>Meaning</th></tr>"
        "<tr><td>fast</td><td>Skip validation</td></tr></table>"
        '<div class="x"><ul><li><a href="/a">Gadget Alpha</a></li>'
        '<li><a href="/b">Gadget Beta</a></li></ul></div>'
    )
    assert "Gadget" not in _text(doc)  # pruned: readability dropped it too
    table = next(b for b in doc.blocks if b.type == "table")
    assert table.data["headers"] == ["Flag", "Meaning"]
    assert "Skip validation" in str(table.data["rows"])
