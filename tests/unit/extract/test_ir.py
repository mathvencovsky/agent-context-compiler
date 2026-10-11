"""IR models, id/normalization helpers and the stable block_id assigner (SPEC §4)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from app.extract.ir import (
    Block,
    Document,
    Link,
    Metadata,
    SourceInfo,
    Warning,
    assign_block_ids,
    base_block_id,
    make_document_id,
    make_version_id,
    normalize_text,
)


def _block(
    text: str,
    *,
    block_type: str = "paragraph",
    order: int = 0,
    heading_path: list[str] | None = None,
) -> Block:
    return Block(
        block_id="",
        type=block_type,  # type: ignore[arg-type]
        order=order,
        level=None,
        heading_path=heading_path or [],
        text=text,
        data={},
        links=[],
        page=None,
    )


def _source() -> SourceInfo:
    return SourceInfo(
        url="https://docs.example.com/auth",
        final_url="https://docs.example.com/auth",
        content_type="text/html",
        fetched_at=datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
        etag=None,
        last_modified=None,
        content_hash="a" * 64,
        render_mode="http",
    )


def _metadata() -> Metadata:
    return Metadata(
        title="Auth",
        lang="en",
        published_at=None,
        modified_at=None,
        jsonld=[{"@type": "TechArticle"}],
    )


def test_models_round_trip() -> None:
    link = Link(text="docs", href="https://docs.example.com/")
    block = Block(
        block_id="b_abc123def456",
        type="paragraph",
        order=0,
        level=None,
        heading_path=["Authentication", "API keys"],
        text="Send the key in the X-Api-Key header.",
        data={},
        links=[link],
        page=None,
    )
    warning = Warning(code="low_text_content", message="short", block_id=None)
    doc = Document(
        document_id=make_document_id("https://docs.example.com/auth"),
        version_id=make_version_id("a" * 64),
        source=_source(),
        metadata=_metadata(),
        blocks=[block],
        warnings=[warning],
    )

    dumped = doc.model_dump()
    restored = Document.model_validate(dumped)
    assert restored == doc
    assert restored.model_dump() == dumped


def test_heading_block_level_and_links() -> None:
    block = Block(
        block_id="b_000000000000",
        type="heading",
        order=1,
        level=2,
        heading_path=["Authentication"],
        text="API keys",
        data={},
        links=[Link(text="here", href="https://example.com/x")],
        page=None,
    )
    assert block.level == 2
    assert block.links[0].href == "https://example.com/x"
    assert Block.model_validate(block.model_dump()) == block


def test_make_document_id_shape() -> None:
    doc_id = make_document_id("https://docs.example.com/auth")
    assert doc_id.startswith("doc_")
    assert len(doc_id) == len("doc_") + 16
    expected = hashlib.sha1(b"https://docs.example.com/auth").hexdigest()[:16]
    assert doc_id == "doc_" + expected


def test_make_version_id_shape() -> None:
    content_hash = hashlib.sha256(b"raw body").hexdigest()
    ver_id = make_version_id(content_hash)
    assert ver_id.startswith("ver_")
    assert len(ver_id) == len("ver_") + 16
    assert ver_id == "ver_" + content_hash[:16]


def test_normalize_text_collapses_whitespace() -> None:
    assert normalize_text("  the\tquick \n brown   fox ") == "the quick brown fox"
    assert normalize_text("already clean") == "already clean"


def test_base_block_id_is_deterministic_and_matches_spec() -> None:
    heading_path = ["Authentication", "API keys"]
    text = "Send the key in the X-Api-Key header."
    first = base_block_id(heading_path, "paragraph", text)
    second = base_block_id(heading_path, "paragraph", text)
    assert first == second
    assert first.startswith("b_")
    assert len(first) == len("b_") + 12
    key = " > ".join(heading_path) + "|" + "paragraph" + "|" + text
    assert first == "b_" + hashlib.sha1(key.encode()).hexdigest()[:12]


def test_base_block_id_varies_with_inputs() -> None:
    a = base_block_id(["A"], "paragraph", "x")
    b = base_block_id(["B"], "paragraph", "x")
    c = base_block_id(["A"], "code", "x")
    d = base_block_id(["A"], "paragraph", "y")
    assert len({a, b, c, d}) == 4


def test_assign_block_ids_disambiguates_identical_blocks() -> None:
    blocks = [_block("same", order=0), _block("same", order=1)]
    assign_block_ids(blocks)
    base = base_block_id([], "paragraph", "same")
    assert blocks[0].block_id == base
    assert blocks[1].block_id == f"{base}_2"


def test_assign_block_ids_distinct_blocks_keep_base() -> None:
    blocks = [_block("one", order=0), _block("two", order=1)]
    assign_block_ids(blocks)
    assert blocks[0].block_id == base_block_id([], "paragraph", "one")
    assert blocks[1].block_id == base_block_id([], "paragraph", "two")


def test_editing_one_of_three_identical_blocks_keeps_earlier_ids() -> None:
    # Version 1: three identical paragraphs -> base, base_2, base_3.
    v1 = [_block("dup", order=i) for i in range(3)]
    assign_block_ids(v1)
    base = base_block_id([], "paragraph", "dup")
    assert [b.block_id for b in v1] == [base, f"{base}_2", f"{base}_3"]

    # Version 2: the LAST identical paragraph is edited; the other two are
    # untouched. Editing one of several identical blocks changes only that
    # block's id and preserves the ids of the copies that precede it in document
    # order (DECISIONS 2026-10-11).
    v2 = [
        _block("dup", order=0),
        _block("dup", order=1),
        _block("dup edited", order=2),
    ]
    assign_block_ids(v2)

    assert v2[0].block_id == v1[0].block_id == base
    assert v2[1].block_id == v1[1].block_id == f"{base}_2"
    # The edited block gets its own, distinct base id.
    assert v2[2].block_id == base_block_id([], "paragraph", "dup edited")
    assert v2[2].block_id != base


def test_editing_a_distinct_block_does_not_shift_identical_blocks() -> None:
    # Two identical blocks separated by a distinct one.
    v1 = [_block("dup", order=0), _block("other", order=1), _block("dup", order=2)]
    assign_block_ids(v1)
    base = base_block_id([], "paragraph", "dup")
    assert v1[0].block_id == base
    assert v1[2].block_id == f"{base}_2"

    # Editing the distinct middle block must not renumber the identical ones,
    # because the occurrence counter is keyed by content, not by position.
    v2 = [
        _block("dup", order=0),
        _block("other edited", order=1),
        _block("dup", order=2),
    ]
    assign_block_ids(v2)
    assert v2[0].block_id == base
    assert v2[2].block_id == f"{base}_2"
