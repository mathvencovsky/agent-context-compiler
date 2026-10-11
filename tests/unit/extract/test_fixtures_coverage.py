"""Main-content <table>/<pre> coverage — the key M3 acceptance check (FEAT-004).

For every real fixture this parses the MAIN CONTENT exactly as the walker selects
it (``_find_main``: the first ``<main>`` then ``<article>`` subtree) and counts
the ``<table>`` and ``<pre>`` elements there. It then runs :func:`extract_html`
and asserts the IR carries AT LEAST that many ``table`` and ``code`` blocks — i.e.
100% of the main-content tables and code blocks reach the IR. The mandatory
readability fallback in FEAT-002 (extended to lists/dls per DECISIONS 2026-10-11)
is what makes this hold even when readability would otherwise drop structural
blocks.

Fixtures with no ``<main>``/``<article>`` (none expected, but handled) count the
whole document body so the check never silently passes on zero.
"""

from __future__ import annotations

import lxml.html
import pytest
from lxml.html import HtmlElement

from app.extract.html import _find_main, _is_code_table, extract_html
from app.extract.ir import SourceInfo
from tests.unit.extract._fixtures import fixture_ids, read_fixture, source_for


def _is_lineno_gutter_pre(pre: HtmlElement) -> bool:
    """True if ``pre`` is a line-number gutter, not real code content.

    Syntax highlighters render a ``highlighttable`` with a ``td.linenos`` /
    ``div.linenodiv`` gutter whose ``<pre>`` holds only "1 2 3 ..." — a
    presentation artifact that duplicates the real code's line count, not a
    distinct code block. It is excluded from the expected count because the
    walker (correctly) does not emit it as a code block.
    """
    for node in (pre, *pre.iterancestors()):
        classes = (node.get("class") or "").lower().split()
        if "linenos" in classes or "linenodiv" in classes:
            return True
    return False


def _main_counts(body: bytes, source: SourceInfo) -> tuple[int, int]:
    """Count data <table> and real <pre> in the main-content subtree.

    Mirrors the walker's main-content selection (``_find_main``) and
    classification so the acceptance count matches what the walker must emit:

    - Syntax-highlighter code tables (``highlighttable``) are CODE, not data
      tables (``_is_code_table``), so they are excluded from the table count and
      their code <pre> are counted toward code.
    - Line-number gutter <pre> are presentation artifacts, excluded from the
      <pre> count (``_is_lineno_gutter_pre``).

    Everything else counts: every data ``<table>`` must become a table block and
    every real ``<pre>`` must become a code block — the 100%-coverage criterion.
    """
    text = body.decode("utf-8", errors="replace")
    tree = lxml.html.fromstring(text)
    main = _find_main(tree)
    scope = main if main is not None else tree

    tables = sum(1 for t in scope.iter("table") if not _is_code_table(t))
    pres = sum(1 for p in scope.iter("pre") if not _is_lineno_gutter_pre(p))
    return tables, pres


@pytest.mark.parametrize("stem", fixture_ids())
def test_main_content_table_and_pre_coverage(stem: str) -> None:
    body = read_fixture(stem)
    source = source_for(stem, body)

    expected_tables, expected_pres = _main_counts(body, source)

    doc = extract_html(body, source)
    ir_tables = sum(1 for b in doc.blocks if b.type == "table")
    ir_code = sum(1 for b in doc.blocks if b.type == "code")

    assert ir_tables >= expected_tables, (
        f"{stem}: main content has {expected_tables} <table> but IR has "
        f"{ir_tables} table blocks"
    )
    assert ir_code >= expected_pres, (
        f"{stem}: main content has {expected_pres} <pre> but IR has "
        f"{ir_code} code blocks"
    )
