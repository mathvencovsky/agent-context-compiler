"""Integration test for lexical + vector search — M7 (SPEC §6 step 2, §5).

Postgres + pgvector are real (compose); the embeddings provider is the
deterministic :class:`FakeEmbeddingProvider`, so nothing touches the network
(CLAUDE.md). The IR is stored through the real :class:`BlobStore` (SeaweedFS)
and indexed with ``index_version`` — exactly the pattern of
``tests/integration/test_indexer.py``.

Two versions of two sources are indexed. The test proves:

* ``lexical_search`` ranks the chunk containing the query terms first and NEVER
  returns a chunk from a version outside the requested set (``ts_rank_cd`` over
  the ``'simple'`` tsvector, version-restricted);
* ``vector_search`` returns chunks ordered by cosine distance to the query
  embedding, restricted to the requested versions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from app.extract.ir import (
    Block,
    Document,
    Metadata,
    SourceInfo,
    assign_block_ids,
    make_document_id,
    make_version_id,
)
from app.index.indexer import index_version
from app.providers.embeddings.fake import FakeEmbeddingProvider
from app.retrieve.lexical import lexical_search
from app.retrieve.vector import vector_search
from app.store.models import DocumentVersion, Source
from tests.integration.conftest import _truncate

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

    from app.store.blobs import BlobStore

pytestmark = pytest.mark.integration

_URL_A = "https://docs.example.com/retrieve-auth"
_URL_B = "https://docs.example.com/retrieve-errors"
_HASH_A = "a" * 64
_HASH_B = "b" * 64


@pytest.fixture
def clean_db(engine: Engine) -> None:
    """Reset all tables before a retrieve test."""
    _truncate(engine)


def _make_block(*, heading_path: list[str], text: str, order: int) -> Block:
    return Block(
        block_id="",  # assigned by assign_block_ids
        type="paragraph",
        order=order,
        level=None,
        heading_path=heading_path,
        text=text,
        data={},
        links=[],
        page=None,
    )


def _document(
    *, url: str, version_id: str, content_hash: str, blocks: list[Block]
) -> Document:
    assign_block_ids(blocks)
    source = SourceInfo(
        url=url,
        final_url=url,
        content_type="text/html",
        fetched_at=datetime(2026, 10, 12, tzinfo=UTC),
        etag=None,
        last_modified=None,
        content_hash=content_hash,
        render_mode="http",
    )
    metadata = Metadata(
        title="Doc", lang="en", published_at=None, modified_at=None, jsonld=[]
    )
    return Document(
        document_id=make_document_id(url),
        version_id=version_id,
        source=source,
        metadata=metadata,
        blocks=blocks,
        warnings=[],
    )


def _seed_version(
    session: Session, *, url: str, version_id: str, content_hash: str
) -> None:
    document_id = make_document_id(url)
    session.add(Source(id=document_id, url_normalized=url, domain="docs.example.com"))
    session.flush()
    session.add(
        DocumentVersion(
            id=version_id,
            source_id=document_id,
            content_hash=content_hash,
            fetched_at=datetime(2026, 10, 12, tzinfo=UTC),
            etag=None,
            last_modified=None,
            render_mode="http",
            raw_blob_key=f"raw/{version_id}",
            ir_blob_key=f"ir/{version_id}.json",
            status="ready",
            warnings=[],
        )
    )
    session.flush()


def _index(
    session_factory: sessionmaker[Session],
    blob_store: BlobStore,
    *,
    url: str,
    content_hash: str,
    blocks: list[Block],
) -> str:
    version_id = make_version_id(content_hash)
    doc = _document(
        url=url, version_id=version_id, content_hash=content_hash, blocks=blocks
    )
    blob_store.put_ir(version_id, doc)
    with session_factory() as session:
        _seed_version(
            session, url=url, version_id=version_id, content_hash=content_hash
        )
        session.commit()
    with session_factory() as session:
        index_version(
            version_id,
            session=session,
            embeddings=FakeEmbeddingProvider(),
            blob_store=blob_store,
        )
        session.commit()
    return version_id


def test_lexical_ranks_query_terms_first_and_restricts_versions(
    clean_db: None,
    session_factory: sessionmaker[Session],
    blob_store: BlobStore,
) -> None:
    """Lexical search ranks the matching chunk first, restricted to versions."""
    version_a = _index(
        session_factory,
        blob_store,
        url=_URL_A,
        content_hash=_HASH_A,
        blocks=[
            _make_block(
                heading_path=["Auth", "Headers"],
                text="Send the Authorization bearer token header on every request.",
                order=0,
            ),
            _make_block(
                heading_path=["Auth", "Rate limits"],
                text="Each account has a daily quota and a burst allowance.",
                order=1,
            ),
        ],
    )
    # A second version (different source) that also mentions "token" — it must
    # never appear when we restrict to version_a only.
    _index(
        session_factory,
        blob_store,
        url=_URL_B,
        content_hash=_HASH_B,
        blocks=[
            _make_block(
                heading_path=["Errors"],
                text="A revoked token returns a 401 unauthorized error.",
                order=0,
            ),
        ],
    )

    with session_factory() as session:
        results = lexical_search(
            session,
            query="authorization bearer token header",
            version_ids=[version_a],
        )

    assert results, "expected at least one lexical match"
    # Positions are 1-based and strictly increasing.
    assert [pos for _, pos in results] == list(range(1, len(results) + 1))
    # Best match is the Authorization/token header chunk.
    top_chunk, top_pos = results[0]
    assert top_pos == 1
    assert "Authorization" in top_chunk.text
    # Version restriction: nothing from version_b (URL_B) leaks in.
    assert all(chunk.version_id == version_a for chunk, _ in results)


def test_lexical_long_question_matches_nothing_under_and_semantics(
    clean_db: None,
    session_factory: sessionmaker[Session],
    blob_store: BlobStore,
) -> None:
    """Characterizes ``websearch_to_tsquery``: every query word must match (AND).

    A short query hits the target chunk; a long natural-language question that
    contains the same terms plus words absent from every chunk returns nothing.
    Pins current behavior (M10a); changing it is a SPEC §6 decision.
    """
    version_a = _index(
        session_factory,
        blob_store,
        url=_URL_A,
        content_hash=_HASH_A,
        blocks=[
            _make_block(
                heading_path=["Auth", "Headers"],
                text="Send the Authorization bearer token header on every request.",
                order=0,
            ),
            _make_block(
                heading_path=["Auth", "Rate limits"],
                text="Each account has a daily quota and a burst allowance.",
                order=1,
            ),
        ],
    )
    # No "or"/"-"/quotes: websearch_to_tsquery would read them as operators.
    long_question = (
        "When I am calling the API from a small Python script running on my "
        "laptop, how exactly should I send the authorization bearer token header "
        "on every request, and what happens when I forget to include it in the "
        "client configuration?"
    )
    assert len(long_question.split()) >= 30

    with session_factory() as session:
        short = lexical_search(session, query="bearer token", version_ids=[version_a])
        long = lexical_search(session, query=long_question, version_ids=[version_a])

    assert len(short) == 1
    assert "Authorization" in short[0][0].text
    assert long == []


def test_vector_search_orders_by_cosine_and_restricts_versions(
    clean_db: None,
    session_factory: sessionmaker[Session],
    blob_store: BlobStore,
) -> None:
    """Vector search returns version-restricted chunks ordered by cosine distance."""
    version_a = _index(
        session_factory,
        blob_store,
        url=_URL_A,
        content_hash=_HASH_A,
        blocks=[
            _make_block(
                heading_path=["Auth", "Headers"],
                text="Send the Authorization bearer token header on every request.",
                order=0,
            ),
            _make_block(
                heading_path=["Auth", "Rate limits"],
                text="Each account has a daily quota and a burst allowance.",
                order=1,
            ),
        ],
    )
    version_b = _index(
        session_factory,
        blob_store,
        url=_URL_B,
        content_hash=_HASH_B,
        blocks=[
            _make_block(
                heading_path=["Errors"],
                text="A revoked token returns a 401 unauthorized error.",
                order=0,
            ),
        ],
    )

    embeddings = FakeEmbeddingProvider()

    with session_factory() as session:
        # Query embedding equal to the first chunk's text embedding -> that
        # chunk is the nearest (cosine distance 0) and must rank first.
        query_text = "Auth > Headers\n\n"  # section-path prefix of chunk 0's text
        # Use the full indexed text of chunk 0 for an exact-match query vector.
        chunk0_text = _chunk_text(session, version_a, ord_=0)
        query_embedding = embeddings.embed([chunk0_text])[0]

        results = vector_search(
            session,
            query_embedding=query_embedding,
            version_ids=[version_a],
        )

    assert results, "expected vector matches"
    # 1-based, strictly increasing positions.
    assert [pos for _, pos in results] == list(range(1, len(results) + 1))
    # The exact-match chunk ranks first (cosine distance 0).
    assert results[0][0].text == chunk0_text
    # Version restriction: only version_a chunks, never version_b.
    assert all(chunk.version_id == version_a for chunk, _ in results)
    assert version_b is not None  # (indexed to prove restriction, see above)
    assert query_text  # section prefix used to document the exact-match query


def _chunk_text(session: Session, version_id: str, *, ord_: int) -> str:
    """Return the indexed text of a specific chunk (helper for exact-match query)."""
    from sqlalchemy import select

    from app.store.models import Chunk

    stmt = select(Chunk).where(Chunk.version_id == version_id, Chunk.ord == ord_)
    chunk = session.scalars(stmt).one()
    return chunk.text
