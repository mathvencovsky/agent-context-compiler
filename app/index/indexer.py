"""Writes a version's chunks to Postgres — M6 (SPEC §5, §7).

``index_version`` reads the stored IR for a ``version_id``, chunks it
(:func:`app.index.chunker.chunk_document`), embeds every chunk's ``text`` in one
batch call through the injected ``EmbeddingProvider`` (the real adapter handles
retry/backoff; tests inject the deterministic ``FakeEmbeddingProvider`` so
nothing touches the network — CLAUDE.md), and persists the rows.

Idempotency is delete-then-insert per ``version_id``: existing rows for the
version are deleted before the new ones are inserted, inside the caller's
transaction (DECISIONS.md 2026-10-12). This is chosen over an upsert because the
chunk ``id`` is a surrogate autoincrement (not provenance), the chunk set for a
version is produced wholesale and deterministically, and reindexing the same IR
must yield the same number of rows. The DB-generated ``tsv`` column is NEVER
written by the app.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete

from app.index.chunker import chunk_document
from app.store.models import Chunk

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.providers.embeddings.base import EmbeddingProvider
    from app.store.blobs import BlobStore


def index_version(
    version_id: str,
    *,
    session: Session,
    embeddings: EmbeddingProvider,
    blob_store: BlobStore,
) -> int:
    """Index one version's chunks into Postgres, idempotently.

    Reads the IR from ``blob_store``, chunks it, embeds every chunk's ``text``
    in a single batch, deletes any existing rows for ``version_id`` and inserts
    the fresh ones. Returns the number of chunks written. ``tsv`` is generated
    by the DB and never set here.
    """
    doc = blob_store.get_ir(version_id)
    chunks = chunk_document(doc)

    # Delete-then-insert: clear any prior rows for this version first so a
    # reindex is idempotent (same IR -> same number of rows).
    session.execute(delete(Chunk).where(Chunk.version_id == version_id))

    if not chunks:
        return 0

    vectors = embeddings.embed([chunk.text for chunk in chunks])
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
                embedding=vector,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
    )
    session.flush()
    return len(chunks)
