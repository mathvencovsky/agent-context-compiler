"""Code language detection and line-number gutters (M10b Phase P, rule H6)."""

from __future__ import annotations

from app.extract.ir import Block
from tests.unit.extract.test_competitor_regressions import _document


def _codes(main_html: str) -> list[Block]:
    return [b for b in _document(main_html).blocks if b.type == "code"]


def _code(main_html: str) -> Block:
    codes = _codes(main_html)
    assert len(codes) == 1, codes
    return codes[0]


def test_sphinx_language_on_ancestor() -> None:
    block = _code(
        '<div class="highlight-python3 notranslate"><div class="highlight">'
        "<pre><span></span>import os\n</pre></div></div>"
    )
    assert block.data["language"] == "python3"
    assert block.data["code"] == "import os\n"


def test_sphinx_default_and_none_name_no_language() -> None:
    for lexer in ("default", "none"):
        block = _code(
            f'<div class="highlight-{lexer} notranslate"><div class="highlight">'
            "<pre>x = 1\n</pre></div></div>"
        )
        assert block.data["language"] is None


def test_other_highlight_classes_are_not_languages() -> None:
    block = _code('<div class="highlight-lines"><pre>x = 1\n</pre></div>')
    assert block.data["language"] is None


def test_mkdocs_vitepress_rouge_language_wrapper() -> None:
    block = _code(
        '<div class="language-yaml highlight"><pre><code>a: 1\n</code></pre></div>'
    )
    assert block.data["language"] == "yaml"


def test_lang_prefix_and_data_attributes() -> None:
    assert _code('<pre><code class="lang-bash">ls\n</code></pre>').data["language"] == (
        "bash"
    )
    assert _code('<pre data-language="sh"><code>ls\n</code></pre>').data[
        "language"
    ] == ("sh")
    assert _code('<pre><code data-lang="toml">a = 1\n</code></pre>').data[
        "language"
    ] == ("toml")


def test_syntaxhighlighter_brush_class() -> None:
    block = _code('<pre class="brush: js notranslate"><code>f()\n</code></pre>')
    assert block.data["language"] == "js"


def test_mediawiki_syntaxhighlight_class() -> None:
    block = _code(
        '<div class="mw-highlight mw-highlight-lang-ini"><pre>a=1</pre></div>'
    )
    assert block.data["language"] == "ini"


def test_hljs_token_classes_never_become_a_language() -> None:
    block = _code(
        '<pre><code class="hljs"><span class="hljs-keyword">def</span> f(): '
        "pass</code></pre>"
    )
    assert block.data["language"] is None


def test_language_of_nested_token_span_is_ignored() -> None:
    # Prism marks embedded script tokens "language-javascript" inside JSX code.
    block = _code(
        '<pre class="gatsby-code-jsx"><code><span class="token script '
        'language-javascript">{f}</span></code></pre>'
    )
    assert block.data["language"] is None


def test_wrapper_language_does_not_spread_to_sibling_blocks() -> None:
    codes = _codes(
        '<div class="language-js"><pre><code>a()</code></pre>'
        "<pre><code>b()</code></pre></div>"
    )
    assert [c.data["language"] for c in codes] == [None, None]


def test_sphinx_inline_linenos_are_stripped() -> None:
    block = _code(
        '<div class="highlight-python3 notranslate"><div class="highlight"><pre>'
        '<span class="linenos">1</span>import os\n'
        '<span class="linenos">2</span>print(1)\n</pre></div></div>'
    )
    assert block.data["code"] == "import os\nprint(1)\n"


def test_data_line_number_and_lineno_class_gutters_are_stripped() -> None:
    block = _code(
        '<pre><code><span class="lineno">1</span>a = 1\n'
        '<span data-line-number="2">2</span>b = 2\n</code></pre>'
    )
    assert block.data["code"] == "a = 1\nb = 2\n"


def test_unclassed_gutter_glued_to_each_line_is_stripped() -> None:
    lines = "".join(
        f'<span class="css-x">{i}</span><span>line_{i}()</span>\n' for i in range(1, 5)
    )
    block = _code(f"<pre>{lines}</pre>")
    assert block.data["code"] == "line_1()\nline_2()\nline_3()\nline_4()\n"


def test_numbers_in_code_are_kept() -> None:
    # Output lines and list literals are numbers on their own lines, not a gutter.
    output = (
        '<pre><span class="gp">&gt;&gt;&gt; </span>for i in r: print(i)\n'
        '<span class="go">1</span>\n<span class="go">2</span>\n'
        '<span class="go">3</span>\n</pre>'
    )
    assert _code(output).data["code"] == ">>> for i in r: print(i)\n1\n2\n3\n"
    literal = (
        "<pre>x = [\n"
        '<span class="mi">1</span>,\n<span class="mi">2</span>,\n'
        '<span class="mi">3</span>,\n]\n</pre>'
    )
    assert _code(literal).data["code"] == "x = [\n1,\n2,\n3,\n]\n"
