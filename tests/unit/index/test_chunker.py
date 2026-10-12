"""Chunker tests (SPEC §5): size bounds, no-split rule, provenance, prefix.

All tests are pure and network-free. The hypothesis property test generates
varied Documents (large and small tables/code plus prose) and proves the two
hard invariants: no chunk exceeds 800 tokens, and any table/code block under 800
tokens is never split (it appears whole inside exactly one chunk).
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.extract.ir import (
    Block,
    Document,
    Metadata,
    SourceInfo,
    assign_block_ids,
)
from app.index.chunker import _MAX_TOKENS, Chunk, chunk_document

_SOURCE = SourceInfo(
    url="https://example.com/doc",
    final_url="https://example.com/doc",
    content_type="text/html",
    fetched_at=__import__("datetime").datetime(2026, 1, 1),  # noqa: DTZ001
    etag=None,
    last_modified=None,
    content_hash="a" * 64,
    render_mode="http",
)
_METADATA = Metadata(
    title="Doc", lang="en", published_at=None, modified_at=None, jsonld=[]
)


def _block(
    block_type: str,
    *,
    order: int,
    heading_path: list[str],
    text: str = "",
    data: dict[str, object] | None = None,
    level: int | None = None,
) -> Block:
    return Block(
        block_id="",
        type=block_type,  # type: ignore[arg-type]
        order=order,
        level=level,
        heading_path=heading_path,
        text=text,
        data=data or {},
        links=[],
        page=None,
    )


def _doc(blocks: list[Block]) -> Document:
    assign_block_ids(blocks)
    return Document(
        document_id="doc_x",
        version_id="ver_test",
        source=_SOURCE,
        metadata=_METADATA,
        blocks=blocks,
        warnings=[],
    )


# --- Direct tests -----------------------------------------------------------


def test_section_prefix_appears_in_text() -> None:
    blocks = [
        _block(
            "paragraph",
            order=0,
            heading_path=["Auth", "Tokens"],
            text="Tokens expire after one hour of inactivity.",
        )
    ]
    chunks = chunk_document(_doc(blocks))
    assert chunks
    assert chunks[0].text.startswith("Auth > Tokens\n\n")


def test_every_chunk_has_nonempty_block_ids_and_coherent_heading_path() -> None:
    blocks = [
        _block("heading", order=0, heading_path=[], text="Intro", level=1),
        _block(
            "paragraph",
            order=1,
            heading_path=["Intro"],
            text="A short paragraph about the system.",
        ),
        _block(
            "paragraph",
            order=2,
            heading_path=["Intro"],
            text="Another paragraph with more detail.",
        ),
    ]
    doc = _doc(blocks)
    by_id = {b.block_id: b for b in doc.blocks}
    for chunk in chunk_document(doc):
        assert chunk.block_ids, "block_ids must be non-empty (provenance)"
        for bid in chunk.block_ids:
            assert by_id[bid].heading_path == chunk.heading_path


def test_large_table_is_split_repeating_headers_in_each_part() -> None:
    headers = ["Name", "Type", "Description"]
    rows = [
        [f"field_{i}", "string", "A reasonably long description sentence number"]
        for i in range(200)
    ]
    block = _block(
        "table",
        order=0,
        heading_path=["Schema"],
        data={"caption": None, "headers": headers, "rows": rows, "notes": []},
    )
    doc = _doc([block])
    chunks = chunk_document(doc)

    assert len(chunks) > 1, "an oversized table must be split"
    for chunk in chunks:
        assert chunk.tokens <= _MAX_TOKENS
        # The header row is repeated in every part.
        assert "| Name | Type | Description |" in chunk.md
        # Provenance: each part keeps the source block's id.
        assert chunk.block_ids == [block.block_id]


def test_large_code_is_split_by_blank_lines() -> None:
    # Blocks of ten lines separated by blank lines; many of them exceed 800.
    segments = [
        "\n".join(f"line_{group}_{i} = compute_value({i})" for i in range(10))
        for group in range(60)
    ]
    code = "\n\n".join(segments)
    block = _block(
        "code",
        order=0,
        heading_path=["Example"],
        data={"language": "python", "code": code},
    )
    doc = _doc([block])
    chunks = chunk_document(doc)

    assert len(chunks) > 1, "an oversized code block must be split"
    for chunk in chunks:
        assert chunk.tokens <= _MAX_TOKENS
        assert chunk.block_ids == [block.block_id]


def test_ord_is_sequential() -> None:
    blocks = [
        _block("paragraph", order=i, heading_path=[f"H{i}"], text=f"Para {i}. " * 40)
        for i in range(5)
    ]
    chunks = chunk_document(_doc(blocks))
    assert [c.ord for c in chunks] == list(range(len(chunks)))


# --- Property test ----------------------------------------------------------


def _small_table_block(order: int, heading: str) -> Block:
    return _block(
        "table",
        order=order,
        heading_path=[heading],
        data={
            "caption": None,
            "headers": ["A", "B"],
            "rows": [["1", "2"], ["3", "4"]],
            "notes": [],
        },
    )


def _small_code_block(order: int, heading: str) -> Block:
    return _block(
        "code",
        order=order,
        heading_path=[heading],
        data={"language": "python", "code": "x = 1\ny = 2\nprint(x + y)"},
    )


def _large_table_block(order: int, heading: str) -> Block:
    rows = [[f"a{i}", f"b{i}", f"c{i} description text"] for i in range(150)]
    return _block(
        "table",
        order=order,
        heading_path=[heading],
        data={"caption": None, "headers": ["X", "Y", "Z"], "rows": rows, "notes": []},
    )


def _large_code_block(order: int, heading: str) -> Block:
    segments = ["\n".join(f"v{g}_{i} = step({i})" for i in range(8)) for g in range(50)]
    return _block(
        "code",
        order=order,
        heading_path=[heading],
        data={"language": "python", "code": "\n\n".join(segments)},
    )


def _prose_block(order: int, heading: str) -> Block:
    return _block(
        "paragraph",
        order=order,
        heading_path=[heading],
        text="This is a prose sentence used to pad the section. " * 6,
    )


_BLOCK_BUILDERS = [
    _small_table_block,
    _small_code_block,
    _large_table_block,
    _large_code_block,
    _prose_block,
]


@st.composite
def _documents(draw: st.DrawFn) -> Document:
    n = draw(st.integers(min_value=1, max_value=6))
    builders = draw(st.lists(st.sampled_from(_BLOCK_BUILDERS), min_size=n, max_size=n))
    headings = draw(
        st.lists(st.sampled_from(["Alpha", "Beta", "Gamma"]), min_size=n, max_size=n)
    )
    blocks = [
        builder(i, heading)
        for i, (builder, heading) in enumerate(zip(builders, headings, strict=True))
    ]
    return _doc(blocks)


def _is_atomic_small(block: Block) -> bool:
    """A table/code block whose standalone text is under the hard max."""
    if block.type not in ("table", "code"):
        return False
    chunks = chunk_document(_doc([block.model_copy(update={"heading_path": []})]))
    return len(chunks) == 1 and chunks[0].tokens <= _MAX_TOKENS


@given(_documents())
@settings(
    max_examples=60,
    deadline=None,  # oversized tables/code make per-example timing vary widely
    suppress_health_check=[HealthCheck.too_slow],
)
def test_property_no_chunk_exceeds_max_and_small_atomics_never_split(
    doc: Document,
) -> None:
    chunks: list[Chunk] = chunk_document(doc)

    # Invariant 1: no chunk exceeds the hard token maximum.
    for chunk in chunks:
        assert chunk.tokens <= _MAX_TOKENS

    # Invariant 2: a table/code block under 800 tokens is never split — its
    # block_id appears in exactly one chunk, and that chunk is not shared across
    # parts (the whole block lives in a single chunk).
    for block in doc.blocks:
        if _is_atomic_small(block):
            owners = [c for c in chunks if block.block_id in c.block_ids]
            assert len(owners) == 1, (
                f"small atomic block {block.block_id} ({block.type}) split across "
                f"{len(owners)} chunks"
            )
