"""Evidence quotes are literal IR substrings and <=300 chars (SPEC §8)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.extract.ir import (
    Block,
    Document,
    Metadata,
    SourceInfo,
    assign_block_ids,
)
from app.retrieve.assemble import _MAX_QUOTE_CHARS, _best_quote
from app.store.models import Chunk

_URL = "https://docs.example.com/auth"
_VERSION = "ver_0123456789abcdef"


def _block(
    *, heading_path: list[str], text: str, order: int, page: int | None
) -> Block:
    return Block(
        block_id="",  # assigned by assign_block_ids
        type="paragraph",
        order=order,
        level=None,
        heading_path=heading_path,
        text=text,
        data={},
        links=[],
        page=page,
    )


def _document(blocks: list[Block]) -> Document:
    assign_block_ids(blocks)
    source = SourceInfo(
        url=_URL,
        final_url=_URL,
        content_type="text/html",
        fetched_at=datetime(2026, 10, 12, tzinfo=UTC),
        etag=None,
        last_modified=None,
        content_hash="0" * 64,
        render_mode="http",
    )
    metadata = Metadata(
        title="Auth", lang="en", published_at=None, modified_at=None, jsonld=[]
    )
    return Document(
        document_id="doc_abc",
        version_id=_VERSION,
        source=source,
        metadata=metadata,
        blocks=blocks,
        warnings=[],
    )


def _chunk(*, block_ids: list[str], page: int | None = None) -> Chunk:
    return Chunk(
        id=1,
        version_id=_VERSION,
        block_ids=block_ids,
        heading_path=["Auth"],
        text="x",
        md="x",
        tokens=1,
        ord=0,
    )


def test_single_block_quote_is_the_block_text() -> None:
    """A chunk backed by one block quotes that block's text literally."""
    block = _block(
        heading_path=["Auth"],
        text="Send the key in the X-Api-Key header.",
        order=0,
        page=None,
    )
    doc = _document([block])
    chunk = _chunk(block_ids=[doc.blocks[0].block_id])

    evidence = _best_quote(chunk, {_VERSION: doc}, "which header authenticates?")

    assert evidence.quote == "Send the key in the X-Api-Key header."
    assert evidence.block_id == doc.blocks[0].block_id
    # Literal substring of the backing block.
    assert evidence.quote in doc.blocks[0].text


def test_multi_block_quote_is_highest_overlap_sentence() -> None:
    """A multi-block chunk quotes the sentence most overlapping the query."""
    block_a = _block(
        heading_path=["Auth"],
        text="This page is about weather. Clouds gather often.",
        order=0,
        page=None,
    )
    block_b = _block(
        heading_path=["Auth"],
        text="Send the key in the X-Api-Key header. Errors return 401.",
        order=1,
        page=None,
    )
    doc = _document([block_a, block_b])
    chunk = _chunk(
        block_ids=[doc.blocks[0].block_id, doc.blocks[1].block_id],
    )

    evidence = _best_quote(chunk, {_VERSION: doc}, "which header carries the api key")

    assert evidence.quote == "Send the key in the X-Api-Key header."
    # The quote is a literal substring of SOME IR block.
    assert any(evidence.quote in b.text for b in doc.blocks)


def test_quote_is_truncated_to_300_chars() -> None:
    """A very long block text is truncated to <=300 chars, still a prefix of it."""
    long_text = "a" * 500
    block = _block(heading_path=["Auth"], text=long_text, order=0, page=None)
    doc = _document([block])
    chunk = _chunk(block_ids=[doc.blocks[0].block_id])

    evidence = _best_quote(chunk, {_VERSION: doc}, "query")

    assert len(evidence.quote) == _MAX_QUOTE_CHARS == 300
    assert long_text.startswith(evidence.quote)


def test_pdf_page_is_carried_through() -> None:
    """The backing block's page number flows into the evidence."""
    block = _block(
        heading_path=["Auth"],
        text="The API key goes in the Authorization header.",
        order=0,
        page=7,
    )
    doc = _document([block])
    chunk = _chunk(block_ids=[doc.blocks[0].block_id])

    evidence = _best_quote(chunk, {_VERSION: doc}, "authorization header")

    assert evidence.page == 7
    assert evidence.quote in doc.blocks[0].text
