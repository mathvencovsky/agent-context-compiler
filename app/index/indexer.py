"""Writes a version's chunks to Postgres — M6 (SPEC §5, §7).

``index_version`` reads the stored IR for a ``version_id``, chunks it
(:func:`app.index.chunker.chunk_document`), embeds every chunk's ``text`` in one
batch call through the injected ``EmbeddingProvider`` (the real adapter handles
retry/backoff; tests inject the deterministic ``FakeEmbeddingProvider`` so
nothing touches the network — CLAUDE.md), and persists the rows.

``assemble`` calls this for every URL on every request, so it reuses what is
already there (DECISIONS.md 2026-10-16):

* if the stored rows for the version match the fresh chunks field by field and
  were embedded by the same model, nothing is written: rows, ``chunk_id``s and
  vectors stay, and the provider is not called;
* otherwise the rows are replaced (delete-then-insert per ``version_id``, inside
  the caller's transaction; DECISIONS.md 2026-10-12), reusing the stored vector
  of any chunk text embedded by the same model and embedding only the rest.

``embedding_model`` records the model on each row; NULL never matches. The
DB-generated ``tsv`` column is NEVER written by the app.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from app.index.chunker import chunk_document
from app.store.models import Chunk

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.providers.embeddings.base import EmbeddingProvider
    from app.store.blobs import BlobStore


def _signature(
    ord_: int,
    text: str,
    md: str,
    block_ids: list[str],
    heading_path: list[str],
    tokens: int,
) -> tuple[object, ...]:
    return (ord_, text, md, tuple(block_ids), tuple(heading_path), tokens)


def index_version(
    version_id: str,
    *,
    session: Session,
    embeddings: EmbeddingProvider,
    blob_store: BlobStore,
) -> int:
    """Index one version's chunks into Postgres, reusing what is unchanged.

    Reads the IR from ``blob_store`` and chunks it. If the stored rows already
    match and were embedded by ``embeddings.model``, returns without writing.
    Otherwise replaces the rows, embedding (in one batch) only the chunk texts
    with no stored vector from the same model. Returns the number of chunks.
    """
    doc = blob_store.get_ir(version_id)
    chunks = chunk_document(doc)
    model = embeddings.model
    stored = session.scalars(
        select(Chunk).where(Chunk.version_id == version_id).order_by(Chunk.ord)
    ).all()
    unchanged = [
        _signature(r.ord, r.text, r.md, r.block_ids, r.heading_path, r.tokens)
        for r in stored
    ] == [
        _signature(c.ord, c.text, c.md, c.block_ids, c.heading_path, c.tokens)
        for c in chunks
    ]
    if unchanged and all(r.embedding_model == model for r in stored):
        return len(chunks)

    vector_by_text = {
        r.text: [float(x) for x in r.embedding]
        for r in stored
        if r.embedding_model == model
    }
    session.execute(delete(Chunk).where(Chunk.version_id == version_id))
    if not chunks:
        return 0

    missing = list(
        dict.fromkeys(c.text for c in chunks if c.text not in vector_by_text)
    )
    if missing:
        vector_by_text.update(zip(missing, embeddings.embed(missing), strict=True))
    session.add_all(
        [
            Chunk(
                version_id=version_id,
                block_ids=chunk.block_ids,
                heading_path=chunk.heading_path,
                text=chunk.text,
                md=chunk.md,
                tokens=chunk.tokens,
                ord=chunk.ord,
                embedding=vector_by_text[chunk.text],
                embedding_model=model,
            )
            for chunk in chunks
        ]
    )
    session.flush()
    return len(chunks)
