"""B2: extraction bugs reported against competitors, checked on NINA's Markdown.

Each case is a small fixture for a defect seen in public issues
(docs/CONCORRENTES.md): code line breaks lost (jina-ai/reader#77), code missing
(firecrawl/firecrawl#215), relative links, broken tables and duplicated text
(unclecode/crawl4ai#1213).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app.extract.html import extract_html
from app.extract.ir import Document, SourceInfo
from app.extract.to_markdown import to_markdown

_PROSE = (
    "<p>" + "This paragraph keeps the main content in place for the test. " * 4 + "</p>"
)


def _document(main_html: str) -> Document:
    html = (
        "<html lang='en'><head><title>T</title></head><body>"
        f"<nav>Home Docs</nav><main><h1>Doc</h1>{_PROSE}{main_html}</main>"
        "<footer>Footer</footer></body></html>"
    )
    source = SourceInfo(
        url="https://docs.example.com/guide/page",
        final_url="https://docs.example.com/guide/page",
        content_type="text/html; charset=utf-8",
        fetched_at=datetime(2026, 10, 17, tzinfo=UTC),
        etag=None,
        last_modified=None,
        content_hash="c" * 64,
        render_mode="http",
    )
    return extract_html(html, source)


def _markdown(main_html: str) -> str:
    return to_markdown(_document(main_html))


def test_highlighted_code_keeps_its_line_breaks() -> None:
    md = _markdown(
        '<div class="highlight"><pre><span></span><span class="k">def</span> '
        '<span class="nf">f</span>():\n    <span class="k">return</span> 1\n'
        "</pre></div>"
    )
    assert "def f():\n    return 1" in md


def test_code_split_in_line_spans_keeps_lines() -> None:
    md = _markdown(
        '<pre class="shiki"><code><span class="line">a = 1</span>\n'
        '<span class="line">b = 2</span></code></pre>'
    )
    assert "a = 1\nb = 2" in md


def test_code_inside_wrappers_is_not_dropped() -> None:
    md = _markdown(
        '<div class="example"><div class="tabs"><div class="tab-panel">'
        "<pre><code>docker run --rm image</code></pre></div></div></div>"
    )
    assert "docker run --rm image" in md


def test_relative_links_become_absolute() -> None:
    # SPEC §4 renders paragraphs as plain text; links live in the IR blocks.
    doc = _document(
        '<p>See <a href="../errors#codes">error codes</a> and '
        '<a href="/api/auth">auth</a> and <a href="tips.html">tips</a>.</p>'
    )
    hrefs = [link.href for block in doc.blocks for link in block.links]
    assert hrefs == [
        "https://docs.example.com/errors#codes",
        "https://docs.example.com/api/auth",
        "https://docs.example.com/guide/tips.html",
    ]


def test_table_rows_and_cells_survive() -> None:
    md = _markdown(
        "<table><thead><tr><th>Flag</th><th>Meaning</th></tr></thead><tbody>"
        "<tr><td><code>--rm</code></td><td>remove the container | on exit</td></tr>"
        "<tr><td><code>-d</code></td><td>run detached</td></tr></tbody></table>"
    )
    rows = [line for line in md.splitlines() if line.startswith("|")]
    assert len(rows) == 4  # header, separator, two rows
    assert "--rm" in rows[2] and "run detached" in rows[3]
    # the "|" inside a cell is escaped, so every row has the same columns
    assert len({len(re.findall(r"(?<!\\)\|", row)) for row in rows}) == 1


def test_nested_wrappers_do_not_duplicate_text() -> None:
    md = _markdown(
        "<div><section><div><p>Only once sentence alpha.</p></div></section></div>"
        "<ul><li><p>Only once sentence beta.</p><ul><li>Nested gamma item.</li>"
        "</ul></li></ul><blockquote><p>Only once sentence delta.</p></blockquote>"
        '<h2><a href="#x">Heading <code>epsilon</code></a></h2>'
    )
    for text in ("alpha", "beta", "gamma", "delta", "epsilon"):
        assert md.count(text) == 1, text
