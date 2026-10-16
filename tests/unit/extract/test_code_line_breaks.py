"""Code line breaks in <pre> (jina-ai/reader#77): pending a decision.

The fix changes the chunk text of ~154 corpus pages (react.dev, MDN), so it is
held until the human decides; see the M10a track B report.
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
