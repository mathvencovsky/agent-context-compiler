"""Integration test for the indexer idempotency — M6 (SPEC §5, §7).

Postgres + pgvector are real (compose); the embeddings provider is the
deterministic :class:`FakeEmbeddingProvider`, so nothing touches the network
(CLAUDE.md). The IR is stored through the real :class:`BlobStore` (SeaweedFS).

The test proves ``index_version`` is idempotent: indexing the same version
twice yields the same number of persisted chunks (delete-then-insert), every
persisted chunk carries non-empty ``block_ids`` (provenance), and the
DB-generated ``tsv`` column is populated by Postgres (never written by the app).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select

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
from app.store.models import Chunk, DocumentVersion, Source
from tests.integration.conftest import _truncate

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

    from app.store.blobs import BlobStore

pytestmark = pytest.mark.integration

_URL = "https://docs.example.com/indexer-guide"


@pytest.fixture
def clean_db(engine: Engine) -> None:
    """Reset all tables (incl. chunks) before an indexer test."""
    _truncate(engine)


def _make_block(
    *, heading_path: list[str], block_type: str, text: str, order: int
) -> Block:
    return Block(
        block_id="",  # assigned by assign_block_ids
        type=block_type,  # type: ignore[arg-type]
        order=order,
        level=None,
        heading_path=heading_path,
        text=text,
        data={},
        links=[],
        page=None,
    )


def _synthetic_document(version_id: str) -> Document:
    """A multi-section IR document for the indexer test."""
    blocks = [
        _make_block(
            heading_path=[],
            block_type="heading",
            text="Guide",
            order=0,
        ),
        _make_block(
            heading_path=["Guide", "Setup"],
            block_type="paragraph",
            text="Install the package and configure the environment variables.",
            order=1,
        ),
        _make_block(
            heading_path=["Guide", "Setup"],
            block_type="paragraph",
            text="Run the database migrations before starting the service.",
            order=2,
        ),
        _make_block(
            heading_path=["Guide", "Usage"],
            block_type="paragraph",
            text="Call the resolve endpoint with the document URL to index it.",
            order=3,
        ),
    ]
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
        title="Guide", lang="en", published_at=None, modified_at=None, jsonld=[]
    )
    return Document(
        document_id=make_document_id(_URL),
        version_id=version_id,
        source=source,
        metadata=metadata,
        blocks=blocks,
        warnings=[],
    )


def _seed_version(session: Session, *, version_id: str) -> None:
    """Insert the Source + DocumentVersion rows the chunks FK requires."""
    document_id = make_document_id(_URL)
    session.add(Source(id=document_id, url_normalized=_URL, domain="docs.example.com"))
    session.flush()
    session.add(
        DocumentVersion(
            id=version_id,
            source_id=document_id,
            content_hash="0" * 64,
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


def _chunk_count(session: Session, version_id: str) -> int:
    stmt = select(func.count()).select_from(Chunk).where(Chunk.version_id == version_id)
    return session.scalar(stmt) or 0


def test_index_version_is_idempotent(
    clean_db: None,
    session_factory: sessionmaker[Session],
    blob_store: BlobStore,
) -> None:
    """Reindexing the same version twice yields the same chunk count."""
    version_id = make_version_id("0" * 64)
    doc = _synthetic_document(version_id)
    blob_store.put_ir(version_id, doc)
    embeddings = FakeEmbeddingProvider()

    with session_factory() as session:
        _seed_version(session, version_id=version_id)
        session.commit()

    # First index.
    with session_factory() as session:
        first = index_version(
            version_id,
            session=session,
            embeddings=embeddings,
            blob_store=blob_store,
        )
        session.commit()
        count_after_first = _chunk_count(session, version_id)

    assert first > 0
    assert count_after_first == first

    # Second index of the same version: delete-then-insert => same count.
    with session_factory() as session:
        second = index_version(
            version_id,
            session=session,
            embeddings=embeddings,
            blob_store=blob_store,
        )
        session.commit()
        count_after_second = _chunk_count(session, version_id)

    assert second == first
    assert count_after_second == count_after_first

    # Every persisted chunk has provenance and a DB-populated tsv.
    with session_factory() as session:
        chunks = list(
            session.scalars(select(Chunk).where(Chunk.version_id == version_id))
        )
        assert len(chunks) == first
        for chunk in chunks:
            assert chunk.block_ids, "block_ids must be non-empty (provenance)"
            assert chunk.tsv is not None, "tsv must be generated by the DB"
