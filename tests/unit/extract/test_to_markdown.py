"""IR → Markdown rendering (SPEC §4, FEAT-002)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.extract.ir import Block, Document, Metadata, SourceInfo
from app.extract.to_markdown import to_markdown


def _source() -> SourceInfo:
    return SourceInfo(
        url="https://docs.example.com/guide",
        final_url="https://docs.example.com/guide",
        content_type="text/html",
        fetched_at=datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
        etag=None,
        last_modified=None,
        content_hash="a" * 64,
        render_mode="http",
    )


def _metadata() -> Metadata:
    return Metadata(
        title=None, lang=None, published_at=None, modified_at=None, jsonld=[]
    )


def _block(
    block_type: str,
    *,
    text: str = "",
    data: dict[str, object] | None = None,
    level: int | None = None,
) -> Block:
    return Block(
        block_id="b_x",
        type=block_type,  # type: ignore[arg-type]
        order=0,
        level=level,
        heading_path=[],
        text=text,
        data=data or {},
        links=[],
        page=None,
    )


def _doc(*blocks: Block) -> Document:
    return Document(
        document_id="doc_x",
        version_id="ver_x",
        source=_source(),
        metadata=_metadata(),
        blocks=list(blocks),
        warnings=[],
    )


def _render(block: Block) -> str:
    return to_markdown(_doc(block))


def test_heading_uses_level() -> None:
    assert _render(_block("heading", text="Intro", level=2)) == "## Intro"


def test_paragraph_passthrough() -> None:
    assert _render(_block("paragraph", text="Hello world.")) == "Hello world."


def test_unordered_list_with_nesting() -> None:
    md = _render(
        _block("list", data={"ordered": False, "items": ["one", "two", "  nested"]})
    )
    assert md == "- one\n- two\n  - nested"


def test_ordered_list_marker() -> None:
    md = _render(_block("list", data={"ordered": True, "items": ["a", "b"]}))
    assert md == "1. a\n1. b"


def test_code_fence_carries_language() -> None:
    md = _render(
        _block("code", data={"language": "python", "code": "def f():\n    return 1\n"})
    )
    assert md == "```python\ndef f():\n    return 1\n```"


def test_code_fence_without_language() -> None:
    md = _render(_block("code", data={"language": None, "code": "plain"}))
    assert md == "```\nplain\n```"


def test_table_is_gfm_with_header() -> None:
    md = _render(
        _block(
            "table",
            data={
                "caption": None,
                "headers": ["A", "B"],
                "rows": [["1", "2"], ["3", "4"]],
                "notes": [],
            },
        )
    )
    assert md == "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |"


def test_table_without_header_synthesizes_one() -> None:
    md = _render(
        _block(
            "table",
            data={"caption": None, "headers": [], "rows": [["x", "y"]], "notes": []},
        )
    )
    assert md == "| col1 | col2 |\n| --- | --- |\n| x | y |"


def test_table_caption_appended() -> None:
    md = _render(
        _block(
            "table",
            data={
                "caption": "My Caption",
                "headers": ["A"],
                "rows": [["1"]],
                "notes": [],
            },
        )
    )
    assert md.endswith("*My Caption*")


def test_blockquote_prefixes_lines() -> None:
    assert (
        _render(_block("blockquote", text="quote", data={"text": "quote"})) == "> quote"
    )


def test_admonition_renders_callout() -> None:
    md = _render(_block("admonition", text="be careful", data={"kind": "warning"}))
    assert md == "> [!WARNING]\n> be careful"


def test_definition_list_renders_terms() -> None:
    md = _render(
        _block(
            "definition_list",
            data={"items": [{"term": "Term", "definition": "Def"}]},
        )
    )
    assert md == "**Term**\n: Def"


def test_blocks_separated_by_blank_line() -> None:
    md = to_markdown(
        _doc(
            _block("heading", text="H", level=1),
            _block("paragraph", text="body"),
        )
    )
    assert md == "# H\n\nbody"
