"""Code line breaks in <pre> (jina-ai/reader#77).

Fixed in ``app.extract.html._raw_code`` (DECISIONS.md 2026-10-17); the fix
changed the chunks of ~154 corpus pages (react.dev, MDN), and the dev tasks that
cite them were re-measured (``bench/m10a_pre_remeasure.py``).
"""

from __future__ import annotations

from tests.unit.extract.test_competitor_regressions import _markdown


def test_code_with_br_line_breaks() -> None:
    md = _markdown("<pre><code>first = 1<br>second = 2<br/>third = 3</code></pre>")
    assert "first = 1\nsecond = 2\nthird = 3" in md


def test_code_lines_as_divs_keep_line_breaks() -> None:
    md = _markdown(
        '<pre><code><div class="cm-line">function f() {</div>'
        '<div class="cm-line">  return 1;</div><div class="cm-line">}</div>'
        "</code></pre>"
    )
    assert "function f() {\n  return 1;\n}" in md
