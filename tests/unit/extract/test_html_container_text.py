"""Container blocks never copy nested code/tables/lists (M10b Phase P, rule H4)."""

from __future__ import annotations

from app.extract.ir import Block
from tests.unit.extract.test_competitor_regressions import _document


def _blocks(main_html: str) -> list[Block]:
    return _document(main_html).blocks[2:]  # skip the fixture's h1 + prose


def _code_count(blocks: list[Block], needle: str) -> int:
    hits = 0
    for b in blocks:
        text = str(b.data.get("code")) if b.type == "code" else b.text
        hits += text.count(needle)
    return hits


def test_code_in_definition_is_emitted_once() -> None:
    blocks = _blocks(
        "<dl><dt>loads(s)</dt><dd><p>Parse s.</p>"
        "<pre><code>json.loads('[1]')</code></pre></dd></dl>"
    )
    assert [b.type for b in blocks] == ["definition_list", "code"]
    assert "json.loads" not in blocks[0].text
    assert blocks[0].data["items"] == [{"term": "loads(s)", "definition": "Parse s."}]
    assert _code_count(blocks, "json.loads") == 1


def test_code_in_admonition_is_emitted_once() -> None:
    blocks = _blocks(
        '<div class="admonition note"><p>Use it like this:</p>'
        "<pre><code>run_the_thing()</code></pre></div>"
    )
    assert [b.type for b in blocks] == ["admonition", "code"]
    assert blocks[0].text == "Use it like this:"
    assert _code_count(blocks, "run_the_thing") == 1


def test_code_in_list_item_is_emitted_once() -> None:
    blocks = _blocks(
        "<ul><li>Install it:<pre><code>pip install thing</code></pre></li>"
        "<li>Done</li></ul>"
    )
    assert [b.type for b in blocks] == ["list", "code"]
    assert blocks[0].data["items"] == ["Install it:", "Done"]
    assert _code_count(blocks, "pip install") == 1


def test_code_in_nested_list_item_is_not_lost() -> None:
    blocks = _blocks(
        "<ol><li>Step<ul><li>Sub step<pre><code>deep_call()</code></pre></li>"
        "</ul></li></ol>"
    )
    assert blocks[0].data["items"] == ["Step", "  Sub step"]
    assert _code_count(blocks, "deep_call") == 1


def test_list_in_blockquote_is_emitted_once() -> None:
    blocks = _blocks("<blockquote><ol><li>First clause</li></ol></blockquote>")
    assert [b.type for b in blocks] == ["list"]
    assert _code_count(blocks, "First clause") == 1


def test_table_in_dd_is_left_out_of_the_definition() -> None:
    blocks = _blocks(
        "<dl><dt>opts</dt><dd><p>Options:</p><table><tr><th>name</th></tr>"
        "<tr><td>verbose</td></tr></table></dd></dl>"
    )
    assert [b.type for b in blocks] == ["definition_list", "table"]
    assert "verbose" not in blocks[0].text
